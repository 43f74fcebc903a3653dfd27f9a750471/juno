import asyncio
from contextlib import suppress
from datetime import datetime, timedelta
from io import BytesIO
import json
from logging import getLogger
from random import choice
from typing import Annotated, List, Optional, Self, Set, cast
from urllib.parse import quote_plus

import aiofiles
from anyio import Path as AsyncPath
from psnawp_api import PSNAWP
from psnawp_api.models.trophies import TrophySummary
from psnawp_api.core.psnawp_exceptions import PSNAWPException
import roblox
from asyncpraw import reddit
from asyncprawcore import AsyncPrawcoreException
from jishaku.functools import executor_function
from cashews import cache
from discord import Embed, File, Member, Message, TextChannel, Thread
from discord.ext.commands import (
    BucketType,
    UserInputError,
    CommandError,
    CooldownMapping,
    Cog,
    command,
    cooldown,
    flag,
    group,
    has_permissions,
    max_concurrency,
    parameter,
)
from discord.utils import format_dt, utcnow
from roblox.client import Client as RobloxClient
from roblox.thumbnails import AvatarThumbnailType
from roblox.users import User as RobloxUser
from roblox.presence import PresenceType
from xxhash import xxh32_hexdigest
from yarl import URL

from bot.core import Context, Juno
from bot.extensions.moderation import DEFAULT_REASON
from bot.extensions.moderation.history.case import Action, Case
from bot.extensions.social.fetcher.letterboxd import LetterboxdUser
from bot.extensions.social.fetcher.pinterest.lens import PinterestLens
from bot.shared import Paginator, cooldowns, quietly_delete
from bot.shared.converters import FlagConverter, Status
from bot.shared.converters.attachment import PartialAttachment
from bot.shared.converters.user import HierarchyMember
from bot.shared.formatter import (
    human_join,
    hyperlink,
    plural,
    short_timespan,
    shorten,
    vowel,
)
from bot.shared.script import Script
from shared_api.wrapper.routes.model import Highlight as InstagramHighlight

from .fetcher import (
    BeatStarsUser,
    CashAppUser,
    KickUser,
    PinterestBoard,
    PinterestUser,
    SoundCloudUser,
    TikTokUser,
    TwitchStream,
    TwitchUser,
    YouTubeUser,
    InstagramUser,
)
from .reposters import REPOSTERS, Reposter
from .reposters.extraction.model import RequestedDownload
from .reposters.tiktok import TikTok as TikTokReposter
from .reposters.instagram import Instagram as InstagramReposter
from .reposters.reddit import Reddit as RedditReposter
from .watcher import Record, Watcher
from .watcher.beatstars import BeatStars as BeatStarsWatcher
from .watcher.kick import Kick as KickWatcher
from .watcher.letterboxd import Letterboxd as LetterboxdWatcher
from .watcher.pinterest import Pinterest as PinterestWatcher
from .watcher.reddit import Reddit as RedditWatcher
from .watcher.soundcloud import SoundCloud as SoundCloudWatcher
from .watcher.tiktok import TikTok as TikTokWatcher, Post as TikTokPost

from .watcher.instagram import Instagram as InstagramWatcher
from .watcher.tumblr import Tumblr as TumblrWatcher
from .watcher.twitch import Twitch as TwitchWatcher
from .watcher.twitter import Twitter as TwitterWatcher
from .watcher.twitter.model import Timeline
from .watcher.twitter.model import User as TwitterUser
from .watcher.youtube import YouTube as YouTubeWatcher
import re

logger = getLogger("bot.social")


class PinterestFlags(FlagConverter):
    board: Optional[str] = flag(description="The board to stream pins from.")
    embeds: Annotated[bool, Status] = flag(
        aliases=["embed"],
        description="Whether to display pins as embeds.",
        default=False,
    )


class TwitterFlags(FlagConverter):
    retweets: Annotated[bool, Status] = flag(
        aliases=["rts", "retweet"],
        description="Whether to dispatch retweets.",
        default=True,
    )
    replies: Annotated[bool, Status] = flag(
        aliases=["reply", "replied"],
        description="Whether to dispatch replies.",
        default=True,
    )
    quotes: Annotated[bool, Status] = flag(
        aliases=["quoted", "quote"],
        description="Whether to dispatch quoted tweets.",
        default=True,
    )


class Subreddit(reddit.Subreddit):
    @classmethod
    @cache(ttl="1h", key="reddit:{argument}")
    async def convert(cls, ctx: Context, argument: str) -> Self:
        if not ctx.bot.reddit:
            raise ValueError("The Reddit client is not available yet")

        async with ctx.typing():
            try:
                subreddit = await ctx.bot.reddit.subreddit(
                    argument.lstrip("r/"),
                    fetch=True,
                )
            except AsyncPrawcoreException as exc:
                raise ValueError(f"No Subreddit found for `{argument}`") from exc

            return subreddit


class Social(Cog):
    reposters: List[Reposter]
    watchers: List[Watcher]

    def __init__(self, bot: Juno) -> None:
        self.bot = bot
        self.reposters = []
        self.watchers = []
        self.roblox_client = RobloxClient(base_url="roproxy.com")
        self.psnawp = PSNAWP(self.bot.config.api.psn)

    async def cog_load(self) -> None:
        for reposter in [
            TikTokReposter,
            InstagramReposter,
            RedditReposter,
        ]:
            self.reposters.append(reposter(self.bot))

        for name, pattern in REPOSTERS.items():
            regex = [pattern] if not isinstance(pattern, list) else pattern
            self.reposters.append(Reposter(self.bot, regex=regex, name=name))

        for watcher in [
            TikTokWatcher,
            InstagramWatcher,
            YouTubeWatcher,
            TwitchWatcher,
            KickWatcher,
            TwitterWatcher,
            SoundCloudWatcher,
            BeatStarsWatcher,
            TumblrWatcher,
            LetterboxdWatcher,
            PinterestWatcher,
            RedditWatcher,
        ]:
            self.watchers.append(watcher(self.bot))

        logger.debug(
            f"Loaded {plural(len(self.reposters)):Social Reposter} and {plural(len(self.watchers)):Social Watcher}"
        )

    async def cog_unload(self) -> None:
        for reposter in self.reposters:
            self.bot.remove_listener(reposter.listener, "on_message_without_command")

        for watcher in self.watchers:
            watcher.scheduler.cancel()

    @group(invoke_without_command=True, aliases=("repost",))
    async def reposter(self, ctx: Context) -> Message:
        """Configure the reposter settings."""

        return await ctx.send_help(ctx.command)

    @reposter.command(name="platforms", aliases=("list",))
    async def reposter_platforms(self, ctx: Context) -> Message:
        """View all available reposters."""

        platforms = human_join(
            [f"`{reposter.name}`" for reposter in self.reposters], final="and"
        )
        return await ctx.respond(f"The available reposters are {platforms}")

    @reposter.command(name="status", aliases=("toggle",))
    @has_permissions(manage_guild=True)
    async def reposter_status(self, ctx: Context) -> Message:
        """Toggle whether reposting is enabled in the server."""

        query = """
        INSERT INTO reposter.config (
            guild_id,
            status
        ) VALUES ($1, FALSE)
        ON CONFLICT (guild_id)
        DO UPDATE SET
            status = NOT config.status
        RETURNING status
        """
        status = cast(bool, await self.bot.db.fetchval(query, ctx.guild.id))
        return await ctx.approve(
            f"{'Now' if status else 'No longer'} reposting messages in this server"
        )

    @reposter.command(name="prefix")
    @has_permissions(manage_guild=True)
    async def reposter_prefix(self, ctx: Context) -> Message:
        """Toggle whether reposting should require a prefix."""

        query = """
        INSERT INTO reposter.config (
            guild_id,
            prefix
        ) VALUES ($1, TRUE)
        ON CONFLICT (guild_id)
        DO UPDATE SET
            prefix = NOT config.prefix
        RETURNING prefix
        """
        status = cast(bool, await self.bot.db.fetchval(query, ctx.guild.id))
        return await ctx.approve(
            f"{'Now' if status else 'No longer'} using `{self.bot.user.name}` as the reposter prefix"
        )

    @reposter.command(name="deletion", aliases=("delete", "del"))
    @has_permissions(manage_guild=True)
    async def reposter_deletion(self, ctx: Context) -> Message:
        """Toggle whether reposted messages should be deleted."""

        query = """
        INSERT INTO reposter.config (
            guild_id,
            deletion
        ) VALUES ($1, TRUE)
        ON CONFLICT (guild_id)
        DO UPDATE SET
            deletion = NOT config.deletion
        RETURNING deletion
        """
        status = cast(bool, await self.bot.db.fetchval(query, ctx.guild.id))
        return await ctx.approve(
            f"Now {'deleting' if status else 'replying to'} reposted messages"
        )

    @reposter.group(
        name="disable",
        aliases=("off", "stop"),
        invoke_without_command=True,
    )
    @has_permissions(manage_guild=True)
    async def reposter_disable(
        self,
        ctx: Context,
        channel: Optional[TextChannel],
        *,
        reposter: Reposter,
    ) -> Message:
        """Disable reposting in a channel or all channels."""

        query = """
        SELECT channel_id
        FROM reposter.disabled
        WHERE guild_id = $1
        AND platform = $2
        """
        channel_ids = cast(
            List[int],
            [
                record["channel_id"]
                for record in await self.bot.db.fetch(
                    query, ctx.guild.id, reposter.name
                )
            ],
        )
        if channel and channel.id in channel_ids:
            return await ctx.warn(
                f"{reposter.name} reposting is already disabled in {channel.mention}"
            )

        elif not channel and all(
            channel.id in channel_ids for channel in ctx.guild.text_channels
        ):
            return await ctx.warn(
                f"{reposter.name} reposting is already disabled in all channels"
            )

        query = """
        INSERT INTO reposter.disabled (
            guild_id,
            channel_id,
            platform
        ) VALUES ($1, $2, $3)
        ON CONFLICT (guild_id, channel_id, platform)
        DO NOTHING
        """
        await self.bot.db.executemany(
            query,
            [
                (ctx.guild.id, channel.id, reposter.name)
                for channel in ([channel] if channel else ctx.guild.text_channels)
            ],
        )

        if channel:
            return await ctx.approve(
                f"Disabled {reposter.name} reposting in {channel.mention}"
            )

        return await ctx.approve(
            f"Disabled {reposter.name} reposting in {plural(len(ctx.guild.text_channels), md='`'):channel}"
        )

    @reposter_disable.command(name="view", aliases=("channels",))
    @has_permissions(manage_guild=True)
    async def reposter_disable_view(self, ctx: Context, reposter: Reposter) -> Message:
        """View all channels a reposter is disabled in."""

        query = """
        SELECT channel_id
        FROM reposter.disabled
        WHERE guild_id = $1
        AND platform = $2
        """
        channel_ids = cast(
            List[int],
            [
                record["channel_id"]
                for record in await self.bot.db.fetch(
                    query, ctx.guild.id, reposter.name
                )
            ],
        )
        channels = [
            f"{channel.mention} [`{channel.id}`]"
            for channel_id in channel_ids
            if (channel := ctx.guild.get_channel(channel_id))
        ]
        if not channels:
            return await ctx.warn(
                f"{reposter.name} reposting is not disabled in any channels"
            )

        embed = Embed(title=f"{reposter.name} Disabled Channels")
        paginator = Paginator(ctx, channels, embed)
        return await paginator.start()

    @reposter_disable.command(name="list")
    @has_permissions(manage_guild=True)
    async def reposter_disable_list(self, ctx: Context) -> Message:
        """View all disabled reposters in the server."""

        query = """
        SELECT platform, ARRAY_AGG(channel_id) AS channel_ids
        FROM reposter.disabled
        WHERE guild_id = $1
        GROUP BY guild_id, platform
        """
        records = await self.bot.db.fetch(query, ctx.guild.id)
        reposters = [
            f"{record['platform']} - {', '.join(channel.mention for channel in channels[:2])}"
            + (f" (+{len(channels) - 2})" if len(channels) > 2 else "")
            for record in records
            if (
                channels := [
                    channel
                    for channel_id in record["channel_ids"]
                    if (channel := ctx.guild.get_channel(channel_id))
                ]
            )
        ]
        if not reposters:
            return await ctx.warn("No reposters are disabled in this server")

        embed = Embed(title="Disabled Reposters")
        paginator = Paginator(ctx, reposters, embed)
        return await paginator.start()

    @reposter.command(name="enable", aliases=("on", "start"))
    @has_permissions(manage_guild=True)
    async def reposter_enable(
        self,
        ctx: Context,
        channel: Optional[TextChannel],
        *,
        reposter: Reposter,
    ) -> Message:
        """Enable reposting in a channel or all channels."""

        query = """
        DELETE FROM reposter.disabled
        WHERE guild_id = $1
        AND platform = $2
        AND channel_id = ANY($3::BIGINT[])
        """
        result = await self.bot.db.execute(
            query,
            ctx.guild.id,
            reposter.name,
            (
                [channel.id]
                if channel
                else [channel.id for channel in ctx.guild.text_channels]
            ),
        )
        if channel and result == "DELETE 0":
            return await ctx.warn(
                f"{reposter.name} reposting is already enabled in {channel.mention}"
            )

        elif not channel and result == "DELETE 0":
            return await ctx.warn(
                f"{reposter.name} reposting is already enabled in all channels"
            )

        if channel:
            return await ctx.approve(
                f"Enabled {reposter.name} reposting in {channel.mention}"
            )

        return await ctx.approve(
            f"Enabled {reposter.name} reposting in {plural(result, md='`'):channel}"
        )

    @executor_function
    def get_psn_user(
        self, username: str
    ) -> tuple[dict, Optional[TrophySummary], Optional[dict]]:
        """Get a PlayStation Network user's profile, trophies, and presence."""

        try:
            user = self.psnawp.user(online_id=username)
        except PSNAWPException as exc:
            raise CommandError("The provided username was not found") from exc

        presence: Optional[dict] = None
        trophy_summary: Optional[TrophySummary] = None
        with suppress(PSNAWPException):
            presence = user.get_presence()["basicPresence"]

        with suppress(PSNAWPException):
            trophy_summary = user.trophy_summary()

        return user.profile(), trophy_summary, presence

    @command(aliases=("playstation", "ps4", "ps5"))
    @cooldown(1, 5, BucketType.user)
    async def psn(self, ctx: Context, username: str) -> Message:
        """View a PlayStation Network user's profile."""

        async with ctx.typing():
            emojis = self.bot.config.emojis.psn
            user, trophy_summary, presence = await self.get_psn_user(username)

        embed = Embed(
            url=f"https://psnprofiles.com/{user['onlineId']}",
            title=user["onlineId"],
            description=user["aboutMe"],
        )
        embed.set_thumbnail(url=user["avatars"][1]["url"])
        if trophy_summary:
            embed.add_field(
                name="Level",
                value=(
                    f"{trophy_summary.trophy_level:,} (`{trophy_summary.progress}%`)"
                    if trophy_summary
                    else "Unknown"
                ),
            )
            trophies = {
                emojis.platinum: trophy_summary.earned_trophies.platinum,
                emojis.gold: trophy_summary.earned_trophies.gold,
                emojis.silver: trophy_summary.earned_trophies.silver,
                emojis.bronze: trophy_summary.earned_trophies.bronze,
            }
            if any(trophies.values()):
                embed.add_field(
                    name="Trophies",
                    value=" ".join(
                        f"{emoji} {value:,}"
                        for emoji, value in trophies.items()
                        if value
                    ),
                )

        if presence:
            embed.add_field(
                name="Presence",
                value=(
                    presence["gameTitleInfoList"][0]["titleName"]
                    + f" on {presence['gameTitleInfoList'][0]['format'].upper()}"
                    if presence["availability"] == "availableToPlay"
                    else f"Last online {format_dt(datetime.fromisoformat(presence['primaryPlatformInfo']['lastOnlineDate']), 'R')} ({presence['primaryPlatformInfo']['platform'].upper()})"
                ),
                inline=False,
            )
        else:
            embed.add_field(
                name="Presence",
                value="Unknown",
                inline=False,
            )

        return await ctx.send(embed=embed)

    @command(aliases=("xbl",))
    @cooldown(1, 5, BucketType.user)
    async def xbox(self, ctx: Context, username: str) -> Message:
        """View an Xbox user's profile."""

        async with ctx.typing():
            response = await self.bot.session.get(
                URL.build(
                    scheme="https",
                    host="playerdb.co",
                    path=f"/api/player/xbox/{username}",
                ),
            )
            if response.status != 200:
                return await ctx.warn("The provided username was not found")

            data = await response.json()
            user = data["data"]["player"]

        embed = Embed(
            url=f"https://xboxgamertag.com/search/{quote_plus(username)}",
            title=user["username"],
            description=user["meta"]["bio"],
        )
        embed.set_thumbnail(url=user["avatar"].replace(" ", "%20"))
        embed.add_field(
            name="Gamerscore",
            value=format(int(user["meta"]["gamerscore"]), ","),
        )
        embed.add_field(name="Reputation", value=user["meta"]["xboxOneRep"])
        embed.add_field(name="Tier", value=user["meta"]["accountTier"])

        return await ctx.send(embed=embed)

    @command(aliases=("steamid",))
    @cooldown(1, 5, BucketType.user)
    async def steam(self, ctx: Context, username: str) -> Message:
        """View a Steam user's profile."""

        async with ctx.typing():
            response = await self.bot.session.get(
                URL.build(
                    scheme="https",
                    host="playerdb.co",
                    path=f"/api/player/steam/{username}",
                ),
            )
            if response.status != 200:
                return await ctx.warn("The provided username was not found")

            data = await response.json()
            user = data["data"]["player"]

        embed = Embed(
            url=user["meta"]["profileurl"],
            title=user["username"],
            description="\n".join(
                [
                    f"**Visibility:** {'Public' if user['meta']['profilestate'] else 'Private'}",
                    f"**ID:** `{user['id']}`",
                ]
            ),
        )
        embed.set_thumbnail(url=user["avatar"])

        return await ctx.send(embed=embed)

    @group(aliases=("git", "gh"), invoke_without_command=True)
    async def github(self, ctx: Context, username: str) -> Message:
        """View a GitHub user's profile."""

        if "/" in username:
            return await self.github_repository(ctx, repository=username)

        async with ctx.typing():
            response = await self.bot.session.get(
                URL.build(
                    scheme="https",
                    host="api.github.com",
                    path=f"/users/{username}",
                ),
            )
            if response.status != 200:
                return await ctx.warn("The provided username was not found")

            data = await response.json()
            response = await self.bot.session.get(data["repos_url"])
            repos = await response.json()

        embed = Embed(
            url=data["html_url"],
            title=data["login"],
            description=data["bio"],
        )
        embed.set_thumbnail(url=data["avatar_url"])
        embed.add_field(name="Followers", value=format(data["followers"], ","))
        embed.add_field(name="Following", value=format(data["following"], ","))
        if repos:
            embed.add_field(
                name=f"Repositories ({data['public_repos']:,})",
                value="\n".join(
                    [
                        f"[`{created_at:%m/%d/%Y}`]"
                        f"({repo['html_url']}) {repo['name']}"
                        for repo in list(
                            sorted(
                                repos,
                                key=lambda repo: repo["stargazers_count"],
                                reverse=True,
                            )
                        )[:3]
                        if (created_at := datetime.fromisoformat(repo["created_at"]))
                    ]
                ),
                inline=False,
            )

        return await ctx.send(embed=embed)

    @github.command(name="repository", aliases=("repo",))
    async def github_repository(self, ctx: Context, *, repository: str) -> Message:
        """View a GitHub repository."""

        if "/" not in repository:
            if " " not in repository:
                return await ctx.warn("The provided repository was not found")

            username, repository = repository.split(" ", 1)
        else:
            username, repository = repository.split("/", 1)

        async with ctx.typing():
            response = await self.bot.session.get(
                URL.build(
                    scheme="https",
                    host="api.github.com",
                    path=f"/repos/{username}/{repository}",
                ),
            )
            if response.status != 200:
                return await ctx.warn("The provided repository was not found")

            data = await response.json()
            response = await self.bot.session.get(
                data["commits_url"].replace("{/sha}", "")
            )
            commits = await response.json()

        embed = Embed(
            url=data["html_url"],
            title=data["full_name"],
            description=data["description"],
        )
        embed.set_thumbnail(url=data["owner"]["avatar_url"])
        embed.add_field(name="Stars", value=format(data["stargazers_count"], ","))
        embed.add_field(name="Forks", value=format(data["forks_count"], ","))
        embed.add_field(name="Issues", value=format(data["open_issues_count"], ","))
        if commits:
            embed.add_field(
                name="Latest Commits",
                value="\n".join(
                    [
                        f"[`{created_at:%m/%d/%Y}`]"
                        f"({commit['html_url']}) {shorten(commit['commit']['message'], 33)}"
                        for commit in commits[:6]
                        if (
                            created_at := datetime.fromisoformat(
                                commit["commit"]["author"]["date"]
                            )
                        )
                    ]
                ),
                inline=False,
            )

        return await ctx.send(embed=embed)

    @group(aliases=("rblx", "rbx"), invoke_without_command=True)
    async def roblox(self, ctx: Context, username: str) -> Message:
        """View a user's Roblox profile."""

        await ctx.typing()
        async with ctx.typing():
            try:
                user = await self.roblox_client.get_user_by_username(username)
            except (roblox.UserNotFound, roblox.BadRequest):
                user = None

            if not isinstance(user, RobloxUser):
                return await ctx.warn("The provided username was not found")

            response = await self.bot.session.get(
                URL.build(
                    scheme="https",
                    host="www.rolimons.com",
                    path=f"/player/{user.id}",
                ),
            )
            html = await response.text()
            chart_data = {"rap": [0]}
            if match := re.search(
                r"var\s+chart_data\s*=\s*(\{.*?\});", html, re.DOTALL
            ):
                chart_data = json.loads(match.group(1))

        name_history: Set[str] = set()
        async for name in user.username_history(max_items=10):
            name_history.add(name)

        async def get_count(method: str) -> int:
            with suppress(roblox.TooManyRequests):
                if method == "followers":
                    return await user.get_follower_count()
                elif method == "following":
                    return await user.get_following_count()

            return 0

        thumbnails, followers, following, presence = await asyncio.gather(
            self.roblox_client.thumbnails.get_user_avatar_thumbnails(
                users=[user.id],
                type=AvatarThumbnailType.full_body,
                size=(420, 420),
            ),
            get_count("followers"),
            get_count("following"),
            user.get_presence(),
        )

        embed = Embed(
            url=f"https://www.roblox.com/users/{user.id}/profile",
            title=(
                f"{user.display_name} (@{user.name})"
                if user.display_name and user.display_name != user.name
                else f"@{user.name}"
            )
            + (" [BANNED]" if user.is_banned else ""),
            description=user.description,
        )
        embed.set_thumbnail(url=thumbnails[0].image_url)

        embed.add_field(
            name="Followers",
            value=format(followers or 0, ","),
        )
        embed.add_field(
            name="Following",
            value=format(following or 0, ","),
        )
        embed.add_field(
            name="RAP",
            value=(
                f"[{chart_data["rap"][-1] if chart_data["rap"] else 0:,}]"
                f"(https://www.rolimons.com/player/{user.id})"
            ),
        )
        embed.add_field(
            name="Created",
            value=format_dt(user.created, "D"),
        )
        embed.add_field(
            name="Last Online",
            value=(
                format_dt(presence.last_online, "R")
                if presence and presence.last_online
                else "Unknown"
            ),
        )
        embed.add_field(
            name="Presence",
            value=(
                presence.place
                if presence and presence.place
                else (
                    presence.last_location
                    if presence and presence.user_presence_type != PresenceType.offline
                    else "Offline"
                )
            ),
        )
        if name_history:
            embed.add_field(
                name="Name History",
                value=", ".join(
                    (f"`{name}`" for name in list(name_history)[:17] if name)
                ),
                inline=False,
            )
            name_history.clear()

        return await ctx.send(embed=embed)

    @roblox.command(
        name="discord",
        aliases=(
            "todiscord",
            "dc",
        ),
    )
    async def roblox_discord(self, ctx: Context, username: str) -> Message:
        """View a user's Discord via their Roblox."""

        await ctx.typing()
        try:
            user = await self.roblox_client.get_user_by_username(username)
        except (roblox.UserNotFound, roblox.BadRequest):
            user = None

        if not isinstance(user, RobloxUser):
            return await ctx.warn("The provided username was not found")

        response = await self.bot.session.get(
            URL.build(
                scheme="https",
                host="api.ropro.io",
                path="/getUserInfoTest.php",
            ),
            params={"userid": user.id},
        )
        if not response.ok:
            return await ctx.warn("An error occurred while fetching the user's Discord")

        data = await response.json()
        discord = data.get("discord")
        if not discord:
            return await ctx.warn("No Discord account was found for this user")

        return await ctx.reply(f"{user.display_name}'s discord is `@{discord}`")

    @command(aliases=("craft",))
    @cooldown(1, 5, BucketType.user)
    async def minecraft(self, ctx: Context, username: str) -> Message:
        """View a user's Minecraft profile."""

        async with ctx.typing():
            response = await self.bot.session.get(
                URL.build(
                    scheme="https",
                    host="crafty.gg",
                    path=f"/players/{username}.json",
                ),
            )
            if response.status != 200:
                return await ctx.warn("The provided username was not found")

            data = await response.json()
            response = await self.bot.session.get(
                URL.build(
                    scheme="https",
                    host="laby.net",
                    path=f"/api/v3/user/{data['uuid']}/game-stats",
                ),
            )
            if response.status != 200:
                stats = {}
            else:
                stats = await response.json()

        embed = Embed(
            url=f"https://namemc.com/profile/{username}",
            title=data["username"],
            description=data["bio"],
        )
        if server := stats.get("most_played_server"):
            first_joined = datetime.fromisoformat(stats["first_joined"])
            last_online = datetime.fromisoformat(stats["last_online"])
            server_info = server["meta"]["info"]
            embed.add_field(
                name="Favorite Server",
                value=(
                    f"**{server['nice_name']}** ([*`{server_info['direct_ip']}`*]({server_info['social']['web']}))"
                    + (
                        f"\n> Last online {format_dt(last_online, 'R')} (*{short_timespan((last_online - first_joined).total_seconds(), max_units=1)} total*)"
                        if (utcnow() - last_online) <= timedelta(days=120)
                        else ""
                    )
                ),
            )

        if data["usernames"][1:]:
            embed.add_field(
                name="Name History",
                value="\n".join(
                    [
                        (
                            f"[`{changed_at:%m/%d/%Y}`](https://namemc.com/search?q={name['username']}) "
                            if changed_at != datetime.fromisoformat(data["created_at"])
                            else f"[`FIRST NAME`](https://namemc.com/search?q={name['username']}) "
                        )
                        + name["username"]
                        + (
                            f" (*{short_timespan((utcnow() - changed_at).total_seconds(), max_units=1)} ago*)"
                            if changed_at != datetime.fromisoformat(data["created_at"])
                            else ""
                        )
                        for name in data["usernames"][1:]
                        if (
                            changed_at := datetime.fromisoformat(
                                name["changed_at"] or data["created_at"]
                            )
                        )
                    ]
                ),
                inline=False,
            )

        if data["capes"]:
            embed.add_field(
                name="Cape" + ("s" if len(data["capes"]) > 1 else ""),
                value="\n".join(
                    [
                        f"[{cape['name']}](https://laby.net/cape/{cape['hash']}) with {plural(cape['players_count'], '`'):player}"
                        for cape in data["capes"]
                    ]
                ),
                inline=False,
            )

        if not embed.description and not embed.fields:
            embed.add_field(name="UUID", value=data["uuid"])

        response = await self.bot.session.get(
            URL.build(
                scheme="https",
                host="render.crafty.gg",
                path=f"/3d/bust/{data['uuid']}",
            ),
        )
        if response.ok:
            buffer = await response.read()
            embed.set_thumbnail(url=f"attachment://{username}.png")
            return await ctx.send(
                embed=embed, file=File(BytesIO(buffer), filename=f"{username}.png")
            )

        embed.set_thumbnail(url=f"https://crafthead.net/avatar/{username}/128")
        return await ctx.send(embed=embed)

    @command(aliases=("cash", "ca"))
    async def cashapp(self, ctx: Context, user: CashAppUser) -> Message:
        """View a Cash App user's profile."""

        embed = Embed(
            url=user.url,
            title=(
                f"{user.display_name} (@{user.username})"
                if user.display_name and user.display_name != user.username
                else f"@{user.username}"
            ),
        )
        embed.color = user.avatar.color
        embed.set_image(url=user.avatar.url or user.qr_code)

        return await ctx.send(embed=embed)

    @group(aliases=("pint", "pin"), invoke_without_command=True)
    async def pinterest(self, ctx: Context, user: PinterestUser) -> Message:
        """View a Pinterest user's profile."""

        embed = Embed(
            url=user.url,
            title=user.display_name,
            description=user.biography,
        )
        embed.set_thumbnail(url=user.avatar_url)
        for name, value in (
            ("Followers", user.followers),
            ("Following", user.following),
            ("Pins", user.pins),
        ):
            embed.add_field(name=name, value=format(value, ","))

        return await ctx.send(embed=embed)

    @pinterest.command(name="visual", aliases=("search", "lens"))
    @cooldown(3, 30, BucketType.user)
    async def pinterest_visual(
        self,
        ctx: Context,
        attachment: PartialAttachment = parameter(
            default=lambda ctx: PartialAttachment.fallback(ctx, ("image",)),
        ),
    ) -> Optional[Message]:
        """Search for similar images using Pinterest Lens."""

        if attachment.format != "image":
            return await ctx.warn("The file must be an image format")

        async with ctx.typing():
            results = await PinterestLens.search(attachment.buffer)
            if not results:
                return await ctx.warn("No results were found for the provided image")

        embeds: List[Embed] = []
        for pin in results:
            embed = Embed(
                url=pin.url,
                title=f"Pinterest Lens ({plural(pin.repin_count):repin})",
                description=pin.description,
            )
            embed.set_image(url=pin.image_url)
            embeds.append(embed)

        paginator = Paginator(ctx, embeds)
        return await paginator.start()

    @pinterest.command(name="add", aliases=("watch", "feed"))
    @has_permissions(manage_channels=True)
    async def pinterest_add(
        self,
        ctx: Context,
        channel: TextChannel | Thread,
        user: PinterestUser,
        *,
        flags: PinterestFlags,
    ) -> Message:
        """Add a channel to receive new pins from a user."""

        if user.private:
            return await ctx.warn(
                f"{user.hyperlink} is a private account and cannot be monitored"
            )

        query = """
        SELECT COUNT(*)
        FROM monitor.pinterest
        WHERE guild_id = $1
        AND channel_id = ANY($2::BIGINT[])
        """
        records = cast(
            int,
            await self.bot.db.fetchval(
                query,
                ctx.guild.id,
                [
                    _channel.id
                    for _channel in ctx.guild.text_channels + list(ctx.guild.threads)
                ],
            ),
        )
        if records >= 50:
            return await ctx.warn(
                "This server has reached the maximum amount of monitored users"
            )

        board: Optional[PinterestBoard] = None
        if flags.board:
            boards = await user.boards()
            if not boards:
                return await ctx.warn(f"{user.hyperlink} doesn't have any boards")

            board = next(
                (
                    board
                    for board in boards
                    if flags.board.lower() in board.name.lower()
                ),
                None,
            )
            if not board:
                return await ctx.warn(
                    "The specified board wasn't found\n>>> "
                    + "\n".join(
                        [
                            f"{board.hyperlink} has {plural(board.pins, '**'):pin}"
                            for board in boards
                        ]
                    )
                )

        await self.bot.db.execute(
            """
            INSERT INTO monitor.pinterest (guild_id, channel_id, user_id, username, board_id, board_name, embeds)
            VALUES ($1, $2, $3, $4, $5, $6, $7)
            ON CONFLICT (guild_id, user_id, board_id)
            DO UPDATE SET
                channel_id = EXCLUDED.channel_id,
                username = EXCLUDED.username,
                board_id = EXCLUDED.board_id,
                board_name = EXCLUDED.board_name,
                embeds = EXCLUDED.embeds
            """,
            ctx.guild.id,
            channel.id,
            user.id,
            user.username,
            board.id if board else "0",
            board.name if board else None,
            flags.embeds,
        )
        return await ctx.approve(
            f"Now notifying {channel.mention} when {user.hyperlink} saves a pin"
            + (f" to the {board.hyperlink} board" if board else "")
        )

    @pinterest.command(name="migrate", aliases=("transfer", "move"))
    @has_permissions(manage_channels=True)
    async def pinterest_migrate(
        self,
        ctx: Context,
        old_channel: TextChannel | Thread,
        new_channel: TextChannel | Thread,
    ) -> Message:
        """Move all Pinterest users from one channel to another."""

        query = """
        UPDATE monitor.pinterest
        SET channel_id = $1
        WHERE guild_id = $2
        AND channel_id = $3
        """
        result = await self.bot.db.execute(
            query,
            new_channel.id,
            ctx.guild.id,
            old_channel.id,
        )
        if result == "UPDATE 0":
            return await ctx.warn(
                f"No Pinterest users are being monitored in {old_channel.mention}"
            )

        return await ctx.approve(
            f"Moved all Pinterest users from {old_channel.mention} to {new_channel.mention}"
        )

    @pinterest.command(name="remove", aliases=("delete", "del", "rm"))
    @has_permissions(manage_channels=True)
    async def pinterest_remove(
        self,
        ctx: Context,
        channel: Optional[TextChannel | Thread],
        user: PinterestUser,
    ) -> Message:
        """Remove a user from a channel's feed."""

        result = await self.bot.db.execute(
            """
            DELETE FROM monitor.pinterest
            WHERE guild_id = $1
            AND user_id = $2
            AND ($3::BIGINT IS NULL OR channel_id = $3)
            """,
            ctx.guild.id,
            user.id,
            channel.id if channel else None,
        )
        if result == "DELETE 0":
            return await ctx.warn(
                f"A feed for {user.hyperlink} wasn't found"
                + (f" in {channel.mention}" if channel else "")
            )

        return await ctx.approve(
            f"No longer sending notifications for {user.hyperlink}"
        )

    @pinterest.command(name="list")
    @has_permissions(manage_channels=True)
    async def pinterest_list(self, ctx: Context) -> Message:
        """View all Pinterest users being monitored."""

        query = "SELECT * FROM monitor.pinterest WHERE guild_id = $1"
        records = cast(list[Record], await self.bot.db.fetch(query, ctx.guild.id))
        channels = [
            f"{channel.mention} - [`@{record['username']}`](https://pinterest.com/{record['username']})"
            + (f" **[{record.get('board_name')}]**" if record.get("board_name") else "")
            for record in records
            if (channel := ctx.guild.get_channel_or_thread(record["channel_id"]))
        ]
        if not channels:
            return await ctx.warn("No Pinterest users are being monitored")

        embed = Embed(title="Pinterest Notifications")
        paginator = Paginator(ctx, channels, embed)
        return await paginator.start()

    @group(aliases=("tw", "x"), invoke_without_command=True)
    async def twitter(self, ctx: Context) -> Message:
        """Notify a channel when a user posts a tweet."""

        return await ctx.send_help(ctx.command)

    @twitter.command(name="add", aliases=("watch", "feed"))
    @max_concurrency(1, BucketType.guild, wait=True)
    @has_permissions(manage_channels=True)
    @cooldown(4, 160, BucketType.guild)
    async def twitter_add(
        self,
        ctx: Context,
        channel: TextChannel | Thread,
        user: TwitterUser,
        *,
        flags: TwitterFlags,
    ) -> Message:
        """Add a channel to receive tweets from a user."""

        if user.blocked_by:
            return await ctx.warn(
                f"{user.hyperlink} has blocked the bot and cannot be monitored"
            )

        elif user.protected:
            return await ctx.warn(
                f"{user.hyperlink} is a protected account and cannot be monitored"
            )

        elif not user.following:
            self.bot.loop.create_task(Timeline.follow(user.id))

        query = """
        SELECT COUNT(*)
        FROM monitor.twitter
        WHERE guild_id = $1
        AND channel_id = ANY($2::BIGINT[])
        """
        records = cast(
            int,
            await self.bot.db.fetchval(
                query,
                ctx.guild.id,
                [
                    _channel.id
                    for _channel in ctx.guild.text_channels + list(ctx.guild.threads)
                ],
            ),
        )
        if records >= 40:
            return await ctx.warn(
                "This server has reached the maximum amount of monitored users"
            )

        await self.bot.db.execute(
            """
            INSERT INTO monitor.twitter (
                guild_id,
                channel_id,
                user_id,
                username,
                retweets,
                replies,
                quotes
            ) VALUES ($1, $2, $3, $4, $5, $6, $7)
            ON CONFLICT (guild_id, user_id)
            DO UPDATE SET
                channel_id = EXCLUDED.channel_id,
                username = EXCLUDED.username,
                retweets = EXCLUDED.retweets,
                replies = EXCLUDED.replies,
                quotes = EXCLUDED.quotes
            """,
            ctx.guild.id,
            channel.id,
            user.id,
            user.username,
            flags.retweets,
            flags.replies,
            flags.quotes,
        )
        return await ctx.approve(
            f"Now notifying {channel.mention} when {user.hyperlink} posts a tweet "
        )

    @twitter.command(name="retweets", aliases=("rts",))
    @has_permissions(manage_channels=True)
    async def twitter_retweets(
        self,
        ctx: Context,
        channel: Optional[TextChannel | Thread],
        user: TwitterUser,
    ) -> Message:
        """Enable or disable retweet notifications."""

        query = """
        UPDATE monitor.twitter
        SET retweets = NOT retweets
        WHERE guild_id = $1
        AND user_id = $2
        RETURNING retweets
        """
        status = cast(
            Optional[bool],
            await self.bot.db.fetchval(query, ctx.guild.id, user.id),
        )
        if status is None:
            return await ctx.warn(f"A feed for {user.hyperlink} wasn't found")

        return await ctx.approve(
            f"{'Now' if status else 'No longer'} sending notifications for retweets from {user.hyperlink}"
        )

    @twitter.command(name="replies", aliases=("reply", "replied"))
    @has_permissions(manage_channels=True)
    async def twitter_replies(
        self,
        ctx: Context,
        channel: Optional[TextChannel | Thread],
        user: TwitterUser,
    ) -> Message:
        """Enable or disable reply notifications."""

        query = """
        UPDATE monitor.twitter
        SET replies = NOT replies
        WHERE guild_id = $1
        AND user_id = $2
        RETURNING replies
        """
        status = cast(
            Optional[bool],
            await self.bot.db.fetchval(query, ctx.guild.id, user.id),
        )
        if status is None:
            return await ctx.warn(f"A feed for {user.hyperlink} wasn't found")

        return await ctx.approve(
            f"{'Now' if status else 'No longer'} sending notifications for replies from {user.hyperlink}"
        )

    @twitter.command(name="quotes", aliases=("quoted", "quote"))
    @has_permissions(manage_channels=True)
    async def twitter_quotes(
        self,
        ctx: Context,
        channel: Optional[TextChannel | Thread],
        user: TwitterUser,
    ) -> Message:
        """Enable or disable quote notifications."""

        query = """
        UPDATE monitor.twitter
        SET quotes = NOT quotes
        WHERE guild_id = $1
        AND user_id = $2
        RETURNING quotes
        """
        status = cast(
            Optional[bool],
            await self.bot.db.fetchval(query, ctx.guild.id, user.id),
        )
        if status is None:
            return await ctx.warn(f"A feed for {user.hyperlink} wasn't found")

        return await ctx.approve(
            f"{'Now' if status else 'No longer'} sending notifications for quotes from {user.hyperlink}"
        )

    @twitter.group(
        name="message",
        aliases=("template", "msg"),
        invoke_without_command=True,
    )
    @has_permissions(manage_messages=True, mention_everyone=True)
    async def twitter_message(
        self,
        ctx: Context,
        channel: Optional[TextChannel | Thread],
        user: Optional[TwitterUser],
        *,
        script: Script,
    ) -> Message:
        """Set a custom message to send when a user tweets.

        The available variables can be found [here](https://egirl.software/variables).
        """

        query = "UPDATE monitor.twitter SET template = $1 WHERE guild_id = $2"
        if not user:
            await ctx.prompt(
                "You did not provide a Twitter user",
                "Would you like to set the message for all users?",
            )

            result = await self.bot.db.execute(query, script.template, ctx.guild.id)
            if result == "UPDATE 0":
                return await ctx.warn("No Twitter users have been modified")

            return await ctx.approve(
                f"Set {vowel(script.format)} tweet message for all Twitter users"
            )

        query += " AND user_id = $3"
        result = await self.bot.db.execute(
            query,
            script.template,
            ctx.guild.id,
            user.id,
        )
        if result == "UPDATE 0":
            return await ctx.warn(f"A feed for {user.hyperlink} wasn't found")

        return await ctx.approve(
            f"Set {vowel(script.format)} message for tweets from {user.hyperlink}"
        )

    @twitter_message.command(name="remove", aliases=("delete", "del", "rm"))
    @has_permissions(manage_messages=True, mention_everyone=True)
    async def twitter_message_remove(
        self,
        ctx: Context,
        channel: Optional[TextChannel | Thread],
        user: Optional[TwitterUser],
    ) -> Message:
        """Remove the custom message for a user or all users."""

        query = "UPDATE monitor.twitter SET template = NULL WHERE guild_id = $1"
        if not user:
            await ctx.prompt(
                "You did not provide a Twitter user",
                "Would you like to reset the message for all users?",
            )

            result = await self.bot.db.execute(query, ctx.guild.id)
            if result == "UPDATE 0":
                return await ctx.warn("No Twitter users have been modified")

            return await ctx.approve("Reset the tweet message for all Twitter users")

        query += " AND user_id = $2"
        result = await self.bot.db.execute(query, ctx.guild.id, user.id)
        if result == "UPDATE 0":
            return await ctx.warn(f"A feed for {user.hyperlink} wasn't found")

        return await ctx.approve(f"Reset the message for tweets from {user.hyperlink}")

    @twitter.command(name="migrate", aliases=("transfer", "move"))
    @has_permissions(manage_channels=True)
    async def twitter_migrate(
        self,
        ctx: Context,
        old_channel: TextChannel | Thread,
        new_channel: TextChannel | Thread,
    ) -> Message:
        """Move all Twitter users from one channel to another."""

        query = """
        UPDATE monitor.twitter
        SET channel_id = $1
        WHERE guild_id = $2
        AND channel_id = $3
        """
        result = await self.bot.db.execute(
            query,
            new_channel.id,
            ctx.guild.id,
            old_channel.id,
        )
        if result == "UPDATE 0":
            return await ctx.warn(
                f"No Twitter users are being monitored in {old_channel.mention}"
            )

        return await ctx.approve(
            f"Moved all Twitter users from {old_channel.mention} to {new_channel.mention}"
        )

    @twitter.command(name="remove", aliases=("delete", "del", "rm"))
    @has_permissions(manage_channels=True)
    async def twitter_remove(
        self,
        ctx: Context,
        channel: Optional[TextChannel | Thread],
        user: TwitterUser,
    ) -> Message:
        """Remove a user from a channel's feed."""

        result = await self.bot.db.execute(
            """
            DELETE FROM monitor.twitter
            WHERE guild_id = $1
            AND user_id = $2
            """,
            ctx.guild.id,
            user.id,
        )
        if result == "DELETE 0":
            return await ctx.warn(f"A feed for {user.hyperlink} wasn't found")

        return await ctx.approve(f"No longer streaming tweets from {user.hyperlink}")

    @twitter.command(name="list")
    @has_permissions(manage_channels=True)
    async def twitter_list(self, ctx: Context) -> Message:
        """View all Twitter users being monitored."""

        query = "SELECT * FROM monitor.twitter WHERE guild_id = $1"
        records = cast(list[Record], await self.bot.db.fetch(query, ctx.guild.id))
        channels = [
            f"{channel.mention} - [`@{record['username']}`](https://twitter.com/@{record['username']})"
            for record in records
            if (channel := ctx.guild.get_channel_or_thread(record["channel_id"]))
        ]
        if not channels:
            return await ctx.warn("No Twitter users are being monitored")

        embed = Embed(title="Twitter Notifications")
        paginator = Paginator(ctx, channels, embed)
        return await paginator.start()

    @group(aliases=("insta", "ig"), invoke_without_command=True)
    @max_concurrency(1, BucketType.guild)
    @cooldowns(
        CooldownMapping.from_cooldown(1, 5, BucketType.user),
        CooldownMapping.from_cooldown(7, 120, BucketType.guild),
    )
    async def instagram(self, ctx: Context, user: InstagramUser) -> Message:
        """View an Instagram user's profile."""

        embed = Embed(url=user.url, title=user.display_name)
        embed.description = (user.biography or "").replace("\n\n", " ")
        embed.set_thumbnail(url=user.avatar.url)

        for link in user.links:
            if link.title:
                embed.description += f"\n🔗 {hyperlink(link.title, link.url)}"
            else:
                embed.description += f"\n🔗 {link.url}"

        for name, value in {
            "Posts": format(user.statistics.posts or 0, ","),
            "Followers": format(user.statistics.followers or 0, ","),
            "Following": format(user.statistics.following or 0, ","),
        }.items():
            embed.add_field(name=name, value=value)

        message = await ctx.send(embed=embed)
        try:
            data = await self.bot.api.instagram.story(user.username)
        except ValueError:
            return message
        
        try:
            await ctx.prompt(
                f"There is a story available for {user.hyperlink}, "
                f"would you like to view {plural(len(data.stories), '`'):item}?",
                reference=None,
            )
        except UserInputError:
            return message

        return await self.instagram_story(ctx, user)
    
    @instagram.command(name="story", aliases=("stories",))
    @max_concurrency(1, BucketType.guild, wait=True)
    @cooldowns(
        CooldownMapping.from_cooldown(1, 5, BucketType.user),
        CooldownMapping.from_cooldown(3, 120, BucketType.guild),
    )
    async def instagram_story(self, ctx: Context, user: InstagramUser) -> Message:
        """View an Instagram user's story."""

        async with ctx.typing():
            try:
                data = await self.bot.api.instagram.story(user.username)
            except ValueError:
                return await ctx.warn(f"{user.hyperlink} doesn't have a story available")

        paginator = Paginator(
            ctx,
            [f"{format_dt(item.taken_at, 'R')} {item.media.url}" for item in data.stories or []],
        )
        return await paginator.start()

    @instagram.command(name="highlights", aliases=("highlight", "hl"))
    @max_concurrency(1, BucketType.guild, wait=True)
    @cooldowns(
        CooldownMapping.from_cooldown(1, 5, BucketType.user),
        CooldownMapping.from_cooldown(3, 120, BucketType.guild),
    )
    async def instagram_highlights(self, ctx: Context, user: InstagramUser) -> Message:
        """View an Instagram user's highlights."""

        if not user.highlights:
            return await ctx.warn(
                f"{user.hyperlink} doesn't have any highlights available"
            )

        highlight = user.highlights[0]
        if len(user.highlights) > 1:
            embed = Embed(
                title="Multiple highlights are available",
                description=(
                    "Please select the highlight from the list below\n"
                    + "\n".join(
                        f"> `{str(index).zfill(2)}` [{highlight.title}]({highlight.url})"
                        for index, highlight in enumerate(user.highlights, start=1)
                    )
                ),
            )
            embed.set_footer(text="Reply with the highlight identifier")
            prompt = await ctx.reply(embed=embed)
            highlight = cast(InstagramHighlight, await ctx.choose_option(user.highlights))
            await quietly_delete(prompt)

        loading = await ctx.respond(
            (
                f"Downloading {user.hyperlink}'s  [{highlight.title}]({highlight.url}) highlights"
                if highlight.title != "Highlights"
                else f"Downloading {user.hyperlink}'s highlights"
            ),
        )
        async with ctx.typing():
            try:
                highlight = await self.bot.api.instagram.highlight(highlight.id)
            except ValueError:
                return await ctx.warn(f"An error occurred while fetching [{highlight.title}]({highlight.url})")
            
            await quietly_delete(loading)

        paginator = Paginator(
            ctx,
            [f"{format_dt(item.taken_at, 'R')} {item.media.url}" for item in highlight.items or []],
        )
        return await paginator.start()

    @instagram.command(name="add", aliases=("watch", "feed"))
    @max_concurrency(1, BucketType.guild, wait=True)
    @has_permissions(manage_channels=True)
    # @cooldown(4, 160, BucketType.guild)
    async def instagram_add(
        self,
        ctx: Context,
        channel: TextChannel | Thread,
        *,
        user: InstagramUser,
    ) -> Message:
        """Add a channel to receive new stories from a user."""

        if user.is_private:
            return await ctx.warn(
                f"{user.hyperlink} is a private account and cannot be monitored"
            )

        query = """
        SELECT COUNT(*)
        FROM monitor.instagram
        WHERE guild_id = $1
        AND channel_id = ANY($2::BIGINT[])
        """
        records = cast(
            int,
            await self.bot.db.fetchval(
                query,
                ctx.guild.id,
                [
                    _channel.id
                    for _channel in ctx.guild.text_channels + list(ctx.guild.threads)
                ],
            ),
        )
        if records >= 10:
            return await ctx.warn(
                "This server has reached the maximum amount of monitored users"
            )

        await self.bot.db.execute(
            """
            INSERT INTO monitor.instagram (
                guild_id,
                channel_id,
                user_id,
                username,
                full_name,
                avatar_url
            )
            VALUES ($1, $2, $3, $4, $5, $6)
            ON CONFLICT (guild_id, user_id)
            DO UPDATE SET
                channel_id = EXCLUDED.channel_id,
                username = EXCLUDED.username,
                full_name = EXCLUDED.full_name,
                avatar_url = EXCLUDED.avatar_url
            """,
            ctx.guild.id,
            channel.id,
            user.pk,
            user.username,
            str(user),
            str(user.profile_pic_url) if user.profile_pic_url else None,
        )
        return await ctx.approve(
            f"Now notifying {channel.mention} when {user.hyperlink} posts a story"
        )

    @instagram.command(name="remove", aliases=("delete", "del", "rm"))
    @has_permissions(manage_channels=True)
    async def instagram_remove(
        self,
        ctx: Context,
        channel: Optional[TextChannel | Thread],
        *,
        user: InstagramUser,
    ) -> Message:
        """Remove a user from a channel's feed."""

        result = await self.bot.db.execute(
            """
            DELETE FROM monitor.instagram
            WHERE guild_id = $1
            AND user_id = $2
            """,
            ctx.guild.id,
            user.pk,
        )
        if result == "DELETE 0":
            return await ctx.warn(f"A feed for {user.hyperlink} wasn't found")

        return await ctx.approve(
            f"No longer sending notifications for {user.hyperlink}"
        )

    @instagram.command(name="list")
    @has_permissions(manage_channels=True)
    async def instagram_list(self, ctx: Context) -> Message:
        """View all Instagram users being monitored."""

        query = "SELECT * FROM monitor.instagram WHERE guild_id = $1"
        records = cast(list[Record], await self.bot.db.fetch(query, ctx.guild.id))
        channels = [
            f"{channel.mention} - [`@{record['username']}`](https://instagram.com/{record['username']})"
            for record in records
            if (channel := ctx.guild.get_channel_or_thread(record["channel_id"]))
        ]
        if not channels:
            return await ctx.warn("No Instagram users are being monitored")

        embed = Embed(title="Instagram Notifications")
        paginator = Paginator(ctx, channels, embed)
        return await paginator.start()

    @group(aliases=("tt",), invoke_without_command=True)
    async def tiktok(self, ctx: Context, user: TikTokUser) -> Message:
        """View a TikTok user's profile."""

        embed = Embed(url=user.url, title=user.display_name)
        embed.description = user.biography.replace("\n\n", "\n")
        embed.set_thumbnail(url=user.avatar_url)

        email_pattern = re.compile(
            r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b"
        )
        embed.description = email_pattern.sub(
            lambda match: f"📧 `{match.group(0)}`", embed.description
        )
        if user.link:
            embed.description += f"\n🔗 {user.link.pretty_url}"

        for name, value in user.statistics.fields.items():
            embed.add_field(name=name, value=value)

        if user.events:
            embed.add_field(
                name="Events",
                value="\n".join(
                    f"{format_dt(event.starts_at, 'd')} - {event.hyperlink}"
                    for event in user.events
                ),
            )

        return await ctx.send(embed=embed)

    @tiktok.command(name="download", aliases=("videos", "posts", "dl"))
    @max_concurrency(1, BucketType.guild)
    @cooldown(3, 120, BucketType.guild)
    async def tiktok_download(self, ctx: Context, user: TikTokUser) -> Message:
        """Download a TikTok user's videos."""

        directory = "/tmp/juno/" + xxh32_hexdigest(f"tiktok:download:{user.id}")
        async with ctx.typing():
            tiktok = cast(TikTokWatcher, self.watchers[0])
            posts = await tiktok.fetch(user.sec_uid)
            if not posts:
                return await ctx.reply("that dude dont have any posts")

            async def download_video(post: TikTokPost) -> Optional[RequestedDownload]:
                assert post.video.url
                filename = f"{directory}/{xxh32_hexdigest(post.id)}.{'mp4' if not post.images else 'png'}"
                if await AsyncPath(filename).exists():
                    return
                
                if post.images:
                    return
                
                buffer = await post.video.read(post.id)
                async with aiofiles.open(filename, "wb") as file:
                    await file.write(buffer.read())

                return RequestedDownload(
                    epoch=int(post.created_at.timestamp()),
                    filepath=filename,
                )

            await AsyncPath(directory).mkdir(exist_ok=True)
            files = sorted(
                list(
                    filter(
                        None,
                        await asyncio.gather(*(download_video(post) for post in posts)),
                    )
                ),
                key=lambda file: file.epoch or 0,
                reverse=True,
            )
            if not files:
                return await ctx.reply("na kid")

        for file in files:
            await file.delete(instant=False)

        paginator = Paginator(
            ctx,
            [file.public_url for file in files],
        )
        return await paginator.start()

    @tiktok.command(name="add", aliases=("watch", "feed"))
    @has_permissions(manage_channels=True)
    async def tiktok_add(
        self,
        ctx: Context,
        channel: TextChannel | Thread,
        *,
        user: TikTokUser,
    ) -> Message:
        """Add a channel to receive posts from a user."""

        if user.is_private:
            return await ctx.warn(
                f"{user.hyperlink} is a private account and cannot be monitored"
            )

        query = """
        SELECT COUNT(*)
        FROM monitor.tiktok
        WHERE guild_id = $1
        AND channel_id = ANY($2::BIGINT[])
        """
        records = cast(
            int,
            await self.bot.db.fetchval(
                query,
                ctx.guild.id,
                [
                    _channel.id
                    for _channel in ctx.guild.text_channels + list(ctx.guild.threads)
                ],
            ),
        )
        if records >= 20:
            return await ctx.warn(
                "This server has reached the maximum amount of monitored users"
            )

        await self.bot.db.execute(
            """
            INSERT INTO monitor.tiktok (guild_id, channel_id, user_id, username)
            VALUES ($1, $2, $3, $4)
            ON CONFLICT (guild_id, user_id)
            DO UPDATE SET
                channel_id = EXCLUDED.channel_id,
                username = EXCLUDED.username
            """,
            ctx.guild.id,
            channel.id,
            user.sec_uid,
            user.username,
        )
        return await ctx.approve(
            f"Now notifying {channel.mention} when {user.hyperlink} posts a video"
        )

    @tiktok.command(name="reposts", aliases=("repost",))
    @has_permissions(manage_channels=True)
    async def tiktok_reposts(
        self,
        ctx: Context,
        channel: Optional[TextChannel | Thread],
        user: TikTokUser,
    ) -> Message:
        """Enable or disable repost notifications."""

        query = """
        UPDATE monitor.tiktok
        SET reposts = NOT reposts
        WHERE guild_id = $1
        AND user_id = $2
        RETURNING reposts
        """
        status = cast(
            Optional[bool],
            await self.bot.db.fetchval(query, ctx.guild.id, user.sec_uid),
        )
        if status is None:
            return await ctx.warn(f"A feed for {user.hyperlink} wasn't found")

        return await ctx.approve(
            f"{'Now' if status else 'No longer'} sending notifications for reposts from {user.hyperlink}"
        )

    @tiktok.group(
        name="lives",
        aliases=("live", "streams"),
        invoke_without_command=True,
    )
    @has_permissions(manage_channels=True)
    async def tiktok_lives(
        self,
        ctx: Context,
        channel: Optional[TextChannel | Thread],
        user: TikTokUser,
    ) -> Message:
        """Enable or disable live notifications."""

        query = """
        UPDATE monitor.tiktok
        SET lives = NOT lives
        WHERE guild_id = $1
        AND user_id = $2
        RETURNING lives
        """
        status = cast(
            Optional[bool],
            await self.bot.db.fetchval(query, ctx.guild.id, user.sec_uid),
        )
        if status is None:
            return await ctx.warn(f"A feed for {user.hyperlink} wasn't found")

        return await ctx.approve(
            f"{'Now' if status else 'No longer'} sending notifications for lives from {user.hyperlink}"
        )

    @tiktok_lives.group(
        name="message",
        aliases=("template", "msg"),
        invoke_without_command=True,
    )
    @has_permissions(manage_messages=True, mention_everyone=True)
    async def tiktok_lives_message(
        self,
        ctx: Context,
        channel: Optional[TextChannel | Thread],
        user: Optional[TikTokUser],
        *,
        script: Script,
    ) -> Message:
        """Set a custom message to send when a user goes live.

        The available variables can be found [here](https://egirl.software/variables).
        """

        query = "UPDATE monitor.tiktok SET live_template = $1 WHERE guild_id = $2"
        if not user:
            await ctx.prompt(
                "You did not provide a TikTok user",
                "Would you like to set the message for all users?",
            )

            result = await self.bot.db.execute(query, script.template, ctx.guild.id)
            if result == "UPDATE 0":
                return await ctx.warn("No TikTok users have been modified")

            return await ctx.approve(
                f"Set {vowel(script.format)} live message for all TikTok users"
            )

        query += " AND user_id = $3"
        result = await self.bot.db.execute(
            query,
            script.template,
            ctx.guild.id,
            user.sec_uid,
        )
        if result == "UPDATE 0":
            return await ctx.warn(f"A feed for {user.hyperlink} wasn't found")

        return await ctx.approve(
            f"Set {vowel(script.format)} message for lives from {user.hyperlink}"
        )

    @tiktok_lives_message.command(name="remove", aliases=("delete", "del", "rm"))
    @has_permissions(manage_messages=True, mention_everyone=True)
    async def tiktok_lives_message_remove(
        self,
        ctx: Context,
        channel: Optional[TextChannel | Thread],
        user: Optional[TikTokUser],
    ) -> Message:
        """Remove the custom message for a user or all users."""

        query = "UPDATE monitor.tiktok SET live_template = NULL WHERE guild_id = $1"
        if not user:
            await ctx.prompt(
                "You did not provide a TikTok user",
                "Would you like to reset the message for all users?",
            )

            result = await self.bot.db.execute(query, ctx.guild.id)
            if result == "UPDATE 0":
                return await ctx.warn("No TikTok users have been modified")

            return await ctx.approve("Reset the live message for all TikTok users")

        query += " AND user_id = $2"
        result = await self.bot.db.execute(query, ctx.guild.id, user.sec_uid)
        if result == "UPDATE 0":
            return await ctx.warn(f"A feed for {user.hyperlink} wasn't found")

        return await ctx.approve(f"Reset the message for lives from {user.hyperlink}")

    @tiktok.group(
        name="message",
        aliases=("template", "msg"),
        invoke_without_command=True,
    )
    @has_permissions(manage_messages=True)
    async def tiktok_message(
        self,
        ctx: Context,
        channel: Optional[TextChannel | Thread],
        user: Optional[TikTokUser],
        *,
        script: Script,
    ) -> Message:
        """Set a custom message to send when a user posts a video.

        The available variables can be found [here](https://egirl.software/variables).
        """

        query = "UPDATE monitor.tiktok SET template = $1 WHERE guild_id = $2"
        if not user:
            await ctx.prompt(
                "You did not provide a TikTok user",
                "Would you like to set the message for all users?",
            )

            result = await self.bot.db.execute(query, script.template, ctx.guild.id)
            if result == "UPDATE 0":
                return await ctx.warn("No TikTok users have been modified")

            return await ctx.approve(
                f"Set {vowel(script.format)} video message for all TikTok users"
            )

        query += " AND user_id = $3"
        result = await self.bot.db.execute(
            query,
            script.template,
            ctx.guild.id,
            user.sec_uid,
        )
        if result == "UPDATE 0":
            return await ctx.warn(f"A feed for {user.hyperlink} wasn't found")

        return await ctx.approve(
            f"Set {vowel(script.format)} message for videos from {user.hyperlink}"
        )

    @tiktok_message.command(name="remove", aliases=("delete", "del", "rm"))
    @has_permissions(manage_messages=True, mention_everyone=True)
    async def tiktok_message_remove(
        self,
        ctx: Context,
        channel: Optional[TextChannel | Thread],
        user: Optional[TikTokUser],
    ) -> Message:
        """Remove the custom message for a user or all users."""

        query = "UPDATE monitor.tiktok SET template = NULL WHERE guild_id = $1"
        if not user:
            await ctx.prompt(
                "You did not provide a TikTok user",
                "Would you like to reset the message for all users?",
            )

            result = await self.bot.db.execute(query, ctx.guild.id)
            if result == "UPDATE 0":
                return await ctx.warn("No TikTok users have been modified")

            return await ctx.approve("Reset the video message for all TikTok users")

        query += " AND user_id = $2"
        result = await self.bot.db.execute(query, ctx.guild.id, user.sec_uid)
        if result == "UPDATE 0":
            return await ctx.warn(f"A feed for {user.hyperlink} wasn't found")

        return await ctx.approve(f"Reset the message for videos from {user.hyperlink}")

    @tiktok.command(name="migrate", aliases=("transfer", "move"))
    @has_permissions(manage_channels=True)
    async def tiktok_migrate(
        self,
        ctx: Context,
        old_channel: TextChannel | Thread,
        new_channel: TextChannel | Thread,
    ) -> Message:
        """Move all TikTok users from one channel to another."""

        query = """
        UPDATE monitor.tiktok
        SET channel_id = $1
        WHERE guild_id = $2
        AND channel_id = $3
        """
        result = await self.bot.db.execute(
            query,
            new_channel.id,
            ctx.guild.id,
            old_channel.id,
        )
        if result == "UPDATE 0":
            return await ctx.warn(
                f"No TikTok users are being monitored in {old_channel.mention}"
            )

        return await ctx.approve(
            f"Moved all TikTok users from {old_channel.mention} to {new_channel.mention}"
        )

    @tiktok.command(name="remove", aliases=("delete", "del", "rm"))
    @has_permissions(manage_channels=True)
    async def tiktok_remove(
        self,
        ctx: Context,
        channel: Optional[TextChannel | Thread],
        user: TikTokUser,
    ) -> Message:
        """Remove a user from a channel's feed."""

        result = await self.bot.db.execute(
            """
            DELETE FROM monitor.tiktok
            WHERE guild_id = $1
            AND user_id = $2
            """,
            ctx.guild.id,
            user.sec_uid,
        )
        if result == "DELETE 0":
            return await ctx.warn(f"A feed for {user.hyperlink} wasn't found")

        return await ctx.approve(f"No longer streaming posts from {user.hyperlink}")

    @tiktok.command(name="list")
    @has_permissions(manage_channels=True)
    async def tiktok_list(self, ctx: Context) -> Message:
        """View all TikTok users being monitored."""

        query = "SELECT * FROM monitor.tiktok WHERE guild_id = $1"
        records = cast(list[Record], await self.bot.db.fetch(query, ctx.guild.id))
        channels = [
            f"{channel.mention} - [`@{record['username']}`](https://www.tiktok.com/@{record['username']})"
            + "".join(
                [f" [`{key.upper()}`]" for key in ("lives", "reposts") if record[key]]  # type: ignore
            )
            for record in records
            if (channel := ctx.guild.get_channel_or_thread(record["channel_id"]))
        ]
        if not channels:
            return await ctx.warn("No TikTok users are being monitored")

        embed = Embed(title="TikTok Notifications")
        paginator = Paginator(ctx, channels, embed)
        return await paginator.start()

    @group(aliases=("yt",), invoke_without_command=True)
    async def youtube(self, ctx: Context, *, query: str) -> Message:
        """Search for a query on YouTube."""

        async with ctx.typing():
            response = await self.bot.session.get(
                URL.build(
                    scheme="https",
                    host="www.googleapis.com",
                    path="/youtube/v3/search",
                ),
                params={
                    "part": "snippet",
                    "key": choice(self.bot.config.api.youtube),
                    "q": query,
                },
            )
            if not response.ok:
                return await ctx.warn("No response was received from the API")

            data = await response.json()
            if not data.get("items"):
                return await ctx.warn("No results were found for the provided query")

        results: List[str] = []
        base_url = "https://www.youtube.com"
        for item in data["items"]:
            if item["id"]["kind"] == "youtube#video":
                results.append(f"{base_url}/watch?v={item['id']['videoId']}")
            elif item["id"]["kind"] == "youtube#channel":
                results.append(f"{base_url}/channel/{item['id']['channelId']}")
            elif item["id"]["kind"] == "youtube#playlist":
                results.append(f"{base_url}/playlist?list={item['id']['playlistId']}")

        paginator = Paginator(ctx, results)
        return await paginator.start()

    @youtube.command(name="add", aliases=("watch", "feed"))
    @has_permissions(manage_channels=True)
    async def youtube_add(
        self,
        ctx: Context,
        channel: TextChannel | Thread,
        *,
        user: YouTubeUser,
    ) -> Message:
        """Add a channel to receive new videos from a user."""

        query = """
        SELECT COUNT(*)
        FROM monitor.youtube
        WHERE guild_id = $1
        AND channel_id = ANY($2::BIGINT[])
        """
        records = cast(
            int,
            await self.bot.db.fetchval(
                query,
                ctx.guild.id,
                [
                    _channel.id
                    for _channel in ctx.guild.text_channels + list(ctx.guild.threads)
                ],
            ),
        )
        if records >= 30:
            return await ctx.warn(
                "This server has reached the maximum amount of monitored users"
            )

        await self.bot.db.execute(
            """
            INSERT INTO monitor.youtube (guild_id, channel_id, user_id, username)
            VALUES ($1, $2, $3, $4)
            ON CONFLICT (guild_id, user_id)
            DO UPDATE SET
                channel_id = EXCLUDED.channel_id,
                username = EXCLUDED.username
            """,
            ctx.guild.id,
            channel.id,
            user.id,
            user.username,
        )
        return await ctx.approve(
            f"Now notifying {channel.mention} when {user.hyperlink} posts a video"
        )

    @youtube.command(name="shorts")
    @has_permissions(manage_channels=True)
    async def youtube_shorts(
        self,
        ctx: Context,
        channel: Optional[TextChannel | Thread],
        *,
        user: YouTubeUser,
    ) -> Message:
        """Enable or disable YouTube shorts notifications."""

        query = """
        UPDATE monitor.youtube
        SET shorts = NOT shorts
        WHERE guild_id = $1
        AND user_id = $2
        RETURNING shorts
        """
        status = cast(
            Optional[bool],
            await self.bot.db.fetchval(query, ctx.guild.id, user.id),
        )
        if status is None:
            return await ctx.warn(f"A feed for {user.hyperlink} wasn't found")

        return await ctx.approve(
            f"{'Now' if status else 'No longer'} sending notifications for YouTube shorts from {user.hyperlink}"
        )

    @youtube.group(
        name="message",
        aliases=("template", "msg"),
        invoke_without_command=True,
    )
    @has_permissions(manage_messages=True, mention_everyone=True)
    async def youtube_message(
        self,
        ctx: Context,
        channel: Optional[TextChannel | Thread],
        user: Optional[YouTubeUser],
        *,
        script: Script,
    ) -> Message:
        """Set a custom message to send when a user posts a video.

        The available variables can be found [here](https://egirl.software/variables).
        """

        query = "UPDATE monitor.youtube SET template = $1 WHERE guild_id = $2"
        if not user:
            await ctx.prompt(
                "You did not provide a YouTube user",
                "Would you like to set the message for all users?",
            )

            result = await self.bot.db.execute(query, script.template, ctx.guild.id)
            if result == "UPDATE 0":
                return await ctx.warn("No YouTube users have been modified")

            return await ctx.approve(
                f"Set {vowel(script.format)} video message for all YouTube users"
            )

        query += " AND user_id = $3"
        result = await self.bot.db.execute(
            query,
            script.template,
            ctx.guild.id,
            user.id,
        )
        if result == "UPDATE 0":
            return await ctx.warn(f"A feed for {user.hyperlink} wasn't found")

        return await ctx.approve(
            f"Set {vowel(script.format)} message for videos from {user.hyperlink}"
        )

    @youtube_message.command(name="remove", aliases=("delete", "del", "rm"))
    @has_permissions(manage_messages=True, mention_everyone=True)
    async def youtube_message_remove(
        self,
        ctx: Context,
        channel: Optional[TextChannel | Thread],
        user: Optional[YouTubeUser],
    ) -> Message:
        """Remove the custom message for a user or all users."""

        query = "UPDATE monitor.youtube SET template = NULL WHERE guild_id = $1"
        if not user:
            await ctx.prompt(
                "You did not provide a YouTube user",
                "Would you like to reset the message for all users?",
            )

            result = await self.bot.db.execute(query, ctx.guild.id)
            if result == "UPDATE 0":
                return await ctx.warn("No YouTube users have been modified")

            return await ctx.approve("Reset the video message for all YouTube users")

        query += " AND user_id = $2"
        result = await self.bot.db.execute(query, ctx.guild.id, user.id)
        if result == "UPDATE 0":
            return await ctx.warn(f"A feed for {user.hyperlink} wasn't found")

        return await ctx.approve(f"Reset the message for videos from {user.hyperlink}")

    @youtube.command(name="migrate", aliases=("transfer", "move"))
    @has_permissions(manage_channels=True)
    async def youtube_migrate(
        self,
        ctx: Context,
        old_channel: TextChannel | Thread,
        new_channel: TextChannel | Thread,
    ) -> Message:
        """Move all YouTube users from one channel to another."""

        query = """
        UPDATE monitor.youtube
        SET channel_id = $1
        WHERE guild_id = $2
        AND channel_id = $3
        """
        result = await self.bot.db.execute(
            query,
            new_channel.id,
            ctx.guild.id,
            old_channel.id,
        )
        if result == "UPDATE 0":
            return await ctx.warn(
                f"No YouTube users are being monitored in {old_channel.mention}"
            )

        return await ctx.approve(
            f"Moved all YouTube users from {old_channel.mention} to {new_channel.mention}"
        )

    @youtube.command(name="remove", aliases=("delete", "del", "rm"))
    @has_permissions(manage_channels=True)
    async def youtube_remove(
        self,
        ctx: Context,
        channel: Optional[TextChannel | Thread],
        *,
        user: YouTubeUser,
    ) -> Message:
        """Remove a user from a channel's feed."""

        result = await self.bot.db.execute(
            """
            DELETE FROM monitor.youtube
            WHERE guild_id = $1
            AND user_id = $2
            """,
            ctx.guild.id,
            user.id,
        )
        if result == "DELETE 0":
            return await ctx.warn(f"A feed for {user.hyperlink} wasn't found")

        return await ctx.approve(
            f"No longer sending notifications for {user.hyperlink}"
        )

    @youtube.command(name="list")
    @has_permissions(manage_channels=True)
    async def youtube_list(self, ctx: Context) -> Message:
        """View all YouTube channles being monitored."""

        query = "SELECT * FROM monitor.youtube WHERE guild_id = $1"
        records = cast(list[Record], await self.bot.db.fetch(query, ctx.guild.id))
        channels = [
            f"{channel.mention} - [**{record['username']}**]"
            f"(https://www.youtube.com/channel/{record['user_id']})"
            + (" **[SHORTS]**" if record.get("shorts") is True else "")
            for record in records
            if (channel := ctx.guild.get_channel_or_thread(record["channel_id"]))
        ]
        if not channels:
            return await ctx.warn("No YouTube channels are being monitored")

        embed = Embed(title="YouTube Notifications")
        paginator = Paginator(ctx, channels, embed)
        return await paginator.start()

    @group(aliases=("ttv",), invoke_without_command=True)
    async def twitch(self, ctx: Context, user: TwitchUser) -> Message:
        """View a Twitch user's profile."""

        embed = Embed(url=user.url, title=user.display_name)
        if user.description:
            embed.description = shorten(user.description, 204)

        embed.set_thumbnail(url=user.avatar_url)
        embed.add_field(name="Created", value=format_dt(user.created_at, "D"))

        return await ctx.send(embed=embed)

    @twitch.command(name="add", aliases=("watch", "feed"))
    @has_permissions(manage_channels=True)
    async def twitch_add(
        self,
        ctx: Context,
        channel: TextChannel | Thread,
        *,
        user: TwitchUser,
    ) -> Message:
        """Add a channel to be notified when a user goes live."""

        query = """
        SELECT COUNT(*)
        FROM monitor.twitch
        WHERE guild_id = $1
        AND channel_id = ANY($2::BIGINT[])
        """
        records = cast(
            int,
            await self.bot.db.fetchval(
                query,
                ctx.guild.id,
                [
                    _channel.id
                    for _channel in ctx.guild.text_channels + list(ctx.guild.threads)
                ],
            ),
        )
        if records >= 10:
            return await ctx.warn(
                "This server has reached the maximum amount of monitored users"
            )

        await self.bot.db.execute(
            """
            INSERT INTO monitor.twitch (guild_id, channel_id, user_id, username)
            VALUES ($1, $2, $3, $4)
            ON CONFLICT (guild_id, user_id)
            DO UPDATE SET
                channel_id = EXCLUDED.channel_id,
                username = EXCLUDED.username
            """,
            ctx.guild.id,
            channel.id,
            user.id,
            user.username,
        )
        return await ctx.approve(
            f"Now notifying {channel.mention} when {user.hyperlink} goes live"
        )

    @twitch.group(
        name="message",
        aliases=("template", "msg"),
        invoke_without_command=True,
    )
    @has_permissions(manage_messages=True, mention_everyone=True)
    async def twitch_message(
        self,
        ctx: Context,
        channel: Optional[TextChannel | Thread],
        user: Optional[TwitchUser],
        *,
        script: Script,
    ) -> Message:
        """Set a custom message to send when a user goes live.

        The available variables can be found [here](https://egirl.software/variables).
        """

        query = "UPDATE monitor.twitch SET template = $1 WHERE guild_id = $2"
        if not user:
            await ctx.prompt(
                "You did not provide a Twitch user",
                "Would you like to set the message for all users?",
            )

            result = await self.bot.db.execute(query, script.template, ctx.guild.id)
            if result == "UPDATE 0":
                return await ctx.warn("No Twitch users have been modified")

            return await ctx.approve(
                f"Set {vowel(script.format)} live message for all Twitch users"
            )

        query += " AND user_id = $3"
        result = await self.bot.db.execute(
            query,
            script.template,
            ctx.guild.id,
            user.id,
        )
        if result == "UPDATE 0":
            return await ctx.warn(f"A feed for {user.hyperlink} wasn't found")

        return await ctx.approve(
            f"Set {vowel(script.format)} message for lives from {user.hyperlink}"
        )

    @twitch_message.command(name="remove", aliases=("delete", "del", "rm"))
    @has_permissions(manage_messages=True, mention_everyone=True)
    async def twitch_message_remove(
        self,
        ctx: Context,
        channel: Optional[TextChannel | Thread],
        user: Optional[TwitchUser],
    ) -> Message:
        """Remove the custom message for a user or all users."""

        query = "UPDATE monitor.twitch SET template = NULL WHERE guild_id = $1"
        if not user:
            await ctx.prompt(
                "You did not provide a Twitch user",
                "Would you like to reset the message for all users?",
            )

            result = await self.bot.db.execute(query, ctx.guild.id)
            if result == "UPDATE 0":
                return await ctx.warn("No Twitch users have been modified")

            return await ctx.approve("Reset the live message for all Twitch users")

        query += " AND user_id = $2"
        result = await self.bot.db.execute(query, ctx.guild.id, user.id)
        if result == "UPDATE 0":
            return await ctx.warn(f"A feed for {user.hyperlink} wasn't found")

        return await ctx.approve(f"Reset the message for lives from {user.hyperlink}")

    @twitch.command(name="migrate", aliases=("transfer", "move"))
    @has_permissions(manage_channels=True)
    async def twitch_migrate(
        self,
        ctx: Context,
        old_channel: TextChannel | Thread,
        new_channel: TextChannel | Thread,
    ) -> Message:
        """Move all Twitch users from one channel to another."""

        query = """
        UPDATE monitor.twitch
        SET channel_id = $1
        WHERE guild_id = $2
        AND channel_id = $3
        """
        result = await self.bot.db.execute(
            query,
            new_channel.id,
            ctx.guild.id,
            old_channel.id,
        )
        if result == "UPDATE 0":
            return await ctx.warn(
                f"No Twitch users are being monitored in {old_channel.mention}"
            )

        return await ctx.approve(
            f"Moved all Twitch users from {old_channel.mention} to {new_channel.mention}"
        )

    @twitch.command(name="remove", aliases=("delete", "del", "rm"))
    @has_permissions(manage_channels=True)
    async def twitch_remove(
        self,
        ctx: Context,
        channel: Optional[TextChannel | Thread],
        user: TwitchUser,
    ) -> Message:
        """Remove a user from a channel's feed."""

        result = await self.bot.db.execute(
            """
            DELETE FROM monitor.twitch
            WHERE guild_id = $1
            AND user_id = $2
            """,
            ctx.guild.id,
            user.id,
        )
        if result == "DELETE 0":
            return await ctx.warn(f"A feed for {user.hyperlink} wasn't found")

        return await ctx.approve(
            f"No longer sending notifications for {user.hyperlink}"
        )

    @twitch.command(name="list")
    @has_permissions(manage_channels=True)
    async def twitch_list(self, ctx: Context) -> Message:
        """View all Twitch users being monitored."""

        query = "SELECT * FROM monitor.twitch WHERE guild_id = $1"
        records = cast(list[Record], await self.bot.db.fetch(query, ctx.guild.id))
        streams = await TwitchStream.fetch(
            self.bot.session,
            [int(record["user_id"]) for record in records],
        )

        channels = [
            f"{channel.mention} - [`@{record['username']}`](https://twitch.tv/{record['username']})"
            + (
                " **[LIVE]**"
                if next(
                    (
                        stream
                        for stream in streams
                        if stream.user_id == int(record["user_id"])
                    ),
                    None,
                )
                else ""
            )
            for record in records
            if (channel := ctx.guild.get_channel_or_thread(record["channel_id"]))
        ]
        if not channels:
            return await ctx.warn("No Twitch users are being monitored")

        embed = Embed(title="Twitch Notifications")
        paginator = Paginator(ctx, channels, embed)
        return await paginator.start()

    @group(invoke_without_command=True)
    @has_permissions(kick_members=True)
    async def kick(
        self,
        ctx: Context,
        member: Annotated[Member, HierarchyMember],
        *,
        reason: str = parameter(default=DEFAULT_REASON),
    ) -> None:
        """Kick a member from the server."""

        if member.premium_since:
            await ctx.prompt(
                f"Are you sure you want to kick {member.mention}?",
                "They are currently boosting the server",
            )

        await member.kick(reason=f"{reason} {ctx.author} ({ctx.author.id})")
        await Case.create(ctx, member, Action.KICK, reason)
        return await ctx.add_check()

    @kick.command(name="add", aliases=("watch", "feed"))
    @has_permissions(manage_channels=True)
    async def kick_add(
        self,
        ctx: Context,
        channel: TextChannel | Thread,
        *,
        user: KickUser,
    ) -> Message:
        """Add a channel to be notified when a user goes live."""

        query = """
        SELECT COUNT(*)
        FROM monitor.kick
        WHERE guild_id = $1
        AND channel_id = ANY($2::BIGINT[])
        """
        records = cast(
            int,
            await self.bot.db.fetchval(
                query,
                ctx.guild.id,
                [
                    _channel.id
                    for _channel in ctx.guild.text_channels + list(ctx.guild.threads)
                ],
            ),
        )
        if records >= 6:
            return await ctx.warn(
                "This server has reached the maximum amount of monitored users"
            )

        await self.bot.db.execute(
            """
            INSERT INTO monitor.kick (guild_id, channel_id, user_id, username)
            VALUES ($1, $2, $3, $4)
            ON CONFLICT (guild_id, user_id)
            DO UPDATE SET
                channel_id = EXCLUDED.channel_id,
                username = EXCLUDED.username
            """,
            ctx.guild.id,
            channel.id,
            user.id,
            user.username,
        )
        return await ctx.approve(
            f"Now notifying {channel.mention} when {user.hyperlink} goes live"
        )

    @kick.group(
        name="message",
        aliases=("template", "msg"),
        invoke_without_command=True,
    )
    @has_permissions(manage_messages=True, mention_everyone=True)
    async def kick_message(
        self,
        ctx: Context,
        channel: Optional[TextChannel | Thread],
        user: Optional[KickUser],
        *,
        script: Script,
    ) -> Message:
        """Set a custom message to send when a user goes live.

        The available variables can be found [here](https://egirl.software/variables).
        """

        query = "UPDATE monitor.kick SET template = $1 WHERE guild_id = $2"
        if not user:
            await ctx.prompt(
                "You did not provide a Kick user",
                "Would you like to set the message for all users?",
            )

            result = await self.bot.db.execute(query, script.template, ctx.guild.id)
            if result == "UPDATE 0":
                return await ctx.warn("No Kick users have been modified")

            return await ctx.approve(
                f"Set {vowel(script.format)} live message for all Kick users"
            )

        query += " AND user_id = $3"
        result = await self.bot.db.execute(
            query,
            script.template,
            ctx.guild.id,
            user.id,
        )
        if result == "UPDATE 0":
            return await ctx.warn(f"A feed for {user.hyperlink} wasn't found")

        return await ctx.approve(
            f"Set {vowel(script.format)} message for lives from {user.hyperlink}"
        )

    @kick_message.command(name="remove", aliases=("delete", "del", "rm"))
    @has_permissions(manage_messages=True, mention_everyone=True)
    async def kick_message_remove(
        self,
        ctx: Context,
        channel: Optional[TextChannel | Thread],
        user: Optional[KickUser],
    ) -> Message:
        """Remove the custom message for a user or all users."""

        query = "UPDATE monitor.kick SET template = NULL WHERE guild_id = $1"
        if not user:
            await ctx.prompt(
                "You did not provide a Kick user",
                "Would you like to reset the message for all users?",
            )

            result = await self.bot.db.execute(query, ctx.guild.id)
            if result == "UPDATE 0":
                return await ctx.warn("No Kick users have been modified")

            return await ctx.approve("Reset the live message for all Kick users")

        query += " AND user_id = $2"
        result = await self.bot.db.execute(query, ctx.guild.id, user.id)
        if result == "UPDATE 0":
            return await ctx.warn(f"A feed for {user.hyperlink} wasn't found")

        return await ctx.approve(f"Reset the message for lives from {user.hyperlink}")

    @kick.command(name="migrate", aliases=("transfer", "move"))
    @has_permissions(manage_channels=True)
    async def kick_migrate(
        self,
        ctx: Context,
        old_channel: TextChannel | Thread,
        new_channel: TextChannel | Thread,
    ) -> Message:
        """Move all Kick users from one channel to another."""

        query = """
        UPDATE monitor.kick
        SET channel_id = $1
        WHERE guild_id = $2
        AND channel_id = $3
        """
        result = await self.bot.db.execute(
            query,
            new_channel.id,
            ctx.guild.id,
            old_channel.id,
        )
        if result == "UPDATE 0":
            return await ctx.warn(
                f"No Kick users are being monitored in {old_channel.mention}"
            )

        return await ctx.approve(
            f"Moved all Kick users from {old_channel.mention} to {new_channel.mention}"
        )

    @kick.command(name="remove", aliases=("delete", "del", "rm"))
    @has_permissions(manage_channels=True)
    async def kick_remove(
        self,
        ctx: Context,
        channel: Optional[TextChannel | Thread],
        user: KickUser,
    ) -> Message:
        """Remove a user from a channel's feed."""

        result = await self.bot.db.execute(
            """
            DELETE FROM monitor.kick
            WHERE guild_id = $1
            AND user_id = $2
            """,
            ctx.guild.id,
            user.id,
        )
        if result == "DELETE 0":
            return await ctx.warn(f"A feed for {user.hyperlink} wasn't found")

        return await ctx.approve(
            f"No longer sending notifications for {user.hyperlink}"
        )

    @kick.command(name="list")
    @has_permissions(manage_channels=True)
    async def kick_list(self, ctx: Context) -> Message:
        """View all Kick users being monitored."""

        query = "SELECT * FROM monitor.kick WHERE guild_id = $1"
        records = cast(list[Record], await self.bot.db.fetch(query, ctx.guild.id))
        channels = [
            f"{channel.mention} - [`@{record['username']}`](https://kick.com/{record['username']})"
            for record in records
            if (channel := ctx.guild.get_channel_or_thread(record["channel_id"]))
        ]
        if not channels:
            return await ctx.warn("No Kick users are being monitored")

        embed = Embed(title="Kick Notifications")
        paginator = Paginator(ctx, channels, embed)
        return await paginator.start()

    @group(aliases=("sc",), invoke_without_command=True)
    async def soundcloud(self, ctx: Context, *, query: str) -> Message:
        """Search for a query on SoundCloud."""

        async with ctx.typing():
            response = await self.bot.session.get(
                URL.build(
                    scheme="https",
                    host="api-v2.soundcloud.com",
                    path="/search/tracks",
                ),
                params={"q": query},
                headers={"Authorization": "OAuth 2-292593-994587358-Af8VbLnc6zIplJ"},
            )
            if not response.ok:
                return await ctx.warn("No response was received from the API")

            data = await response.json()
            if not data.get("collection"):
                return await ctx.warn("No results were found for the provided query")

        results = [track["permalink_url"] for track in data["collection"]]
        paginator = Paginator(ctx, results)
        return await paginator.start()

    @soundcloud.command(name="add", aliases=("watch", "feed"))
    @has_permissions(manage_channels=True)
    async def soundcloud_add(
        self,
        ctx: Context,
        channel: TextChannel | Thread,
        *,
        user: SoundCloudUser,
    ) -> Message:
        """Add a channel to receive new tracks from a user."""

        query = """
        SELECT COUNT(*)
        FROM monitor.soundcloud
        WHERE guild_id = $1
        AND channel_id = ANY($2::BIGINT[])
        """
        records = cast(
            int,
            await self.bot.db.fetchval(
                query,
                ctx.guild.id,
                [
                    _channel.id
                    for _channel in ctx.guild.text_channels + list(ctx.guild.threads)
                ],
            ),
        )
        if records >= 6:
            return await ctx.warn(
                "This server has reached the maximum amount of monitored users"
            )

        await self.bot.db.execute(
            """
            INSERT INTO monitor.soundcloud (guild_id, channel_id, user_id, username)
            VALUES ($1, $2, $3, $4)
            ON CONFLICT (guild_id, user_id)
            DO UPDATE SET
                channel_id = EXCLUDED.channel_id,
                username = EXCLUDED.username
            """,
            ctx.guild.id,
            channel.id,
            user.id,
            user.username,
        )
        return await ctx.approve(
            f"Now notifying {channel.mention} when {user.hyperlink} posts a track"
        )

    @soundcloud.group(
        name="message",
        aliases=("template", "msg"),
        invoke_without_command=True,
    )
    @has_permissions(manage_messages=True, mention_everyone=True)
    async def soundcloud_message(
        self,
        ctx: Context,
        channel: Optional[TextChannel | Thread],
        user: Optional[SoundCloudUser],
        *,
        script: Script,
    ) -> Message:
        """Set a custom message to send when a user posts a track.

        The available variables can be found [here](https://egirl.software/variables).
        """

        query = "UPDATE monitor.soundcloud SET template = $1 WHERE guild_id = $2"
        if not user:
            await ctx.prompt(
                "You did not provide a SoundCloud user",
                "Would you like to set the message for all users?",
            )

            result = await self.bot.db.execute(query, script.template, ctx.guild.id)
            if result == "UPDATE 0":
                return await ctx.warn("No SoundCloud users have been modified")

            return await ctx.approve(
                f"Set {vowel(script.format)} track message for all SoundCloud users"
            )

        query += " AND user_id = $3"
        result = await self.bot.db.execute(
            query,
            script.template,
            ctx.guild.id,
            user.id,
        )
        if result == "UPDATE 0":
            return await ctx.warn(f"A feed for {user.hyperlink} wasn't found")

        return await ctx.approve(
            f"Set {vowel(script.format)} message for tracks from {user.hyperlink}"
        )

    @soundcloud_message.command(name="remove", aliases=("delete", "del", "rm"))
    @has_permissions(manage_messages=True, mention_everyone=True)
    async def soundcloud_message_remove(
        self,
        ctx: Context,
        channel: Optional[TextChannel | Thread],
        user: Optional[SoundCloudUser],
    ) -> Message:
        """Remove the custom message for a user or all users."""

        query = "UPDATE monitor.soundcloud SET template = NULL WHERE guild_id = $1"
        if not user:
            await ctx.prompt(
                "You did not provide a SoundCloud user",
                "Would you like to reset the message for all users?",
            )

            result = await self.bot.db.execute(query, ctx.guild.id)
            if result == "UPDATE 0":
                return await ctx.warn("No SoundCloud users have been modified")

            return await ctx.approve("Reset the track message for all SoundCloud users")

        query += " AND user_id = $2"
        result = await self.bot.db.execute(query, ctx.guild.id, user.id)
        if result == "UPDATE 0":
            return await ctx.warn(f"A feed for {user.hyperlink} wasn't found")

        return await ctx.approve(f"Reset the message for tracks from {user.hyperlink}")

    @soundcloud.command(name="migrate", aliases=("transfer", "move"))
    @has_permissions(manage_channels=True)
    async def soundcloud_migrate(
        self,
        ctx: Context,
        old_channel: TextChannel | Thread,
        new_channel: TextChannel | Thread,
    ) -> Message:
        """Move all SoundCloud users from one channel to another."""

        query = """
        UPDATE monitor.soundcloud
        SET channel_id = $1
        WHERE guild_id = $2
        AND channel_id = $3
        """
        result = await self.bot.db.execute(
            query,
            new_channel.id,
            ctx.guild.id,
            old_channel.id,
        )
        if result == "UPDATE 0":
            return await ctx.warn(
                f"No SoundCloud users are being monitored in {old_channel.mention}"
            )

        return await ctx.approve(
            f"Moved all SoundCloud users from {old_channel.mention} to {new_channel.mention}"
        )

    @soundcloud.command(name="remove", aliases=("delete", "del", "rm"))
    @has_permissions(manage_channels=True)
    async def soundcloud_remove(
        self,
        ctx: Context,
        channel: Optional[TextChannel | Thread],
        *,
        user: SoundCloudUser,
    ) -> Message:
        """Remove a user from a channel's feed."""

        result = await self.bot.db.execute(
            """
            DELETE FROM monitor.soundcloud
            WHERE guild_id = $1
            AND user_id = $2
            """,
            ctx.guild.id,
            user.id,
        )
        if result == "DELETE 0":
            return await ctx.warn(f"A feed for {user.hyperlink} wasn't found")

        return await ctx.approve(
            f"No longer sending notifications for {user.hyperlink}"
        )

    @soundcloud.command(name="list")
    @has_permissions(manage_channels=True)
    async def soundcloud_list(self, ctx: Context) -> Message:
        """View all SoundCloud users being monitored."""

        query = "SELECT * FROM monitor.soundcloud WHERE guild_id = $1"
        records = cast(list[Record], await self.bot.db.fetch(query, ctx.guild.id))
        channels = [
            f"{channel.mention} - [`@{record['username']}`](https://soundcloud.com/{record['username']})"
            for record in records
            if (channel := ctx.guild.get_channel_or_thread(record["channel_id"]))
        ]
        if not channels:
            return await ctx.warn("No SoundCloud users are being monitored")

        embed = Embed(title="SoundCloud Notifications")
        paginator = Paginator(ctx, channels, embed)
        return await paginator.start()

    @group(aliases=("beatstar", "bstars", "bstar"), invoke_without_command=True)
    async def beatstars(self, ctx: Context, user: BeatStarsUser) -> Message:
        """View a BeatStars user's profile."""

        embed = Embed(
            url=user.url,
            title=user.display_name,
            description=user.biography,
        )
        embed.set_thumbnail(url=user.avatar_url)

        for name, value in (
            ("Followers", user.followers_count),
            ("Listens", user.plays_count),
        ):
            embed.add_field(name=name, value=format(value, ","))

        if user.location:
            embed.add_field(name="Location", value=user.location, inline=False)

        return await ctx.send(embed=embed)

    @beatstars.command(name="add", aliases=("watch", "feed"))
    @has_permissions(manage_channels=True)
    async def beatstars_add(
        self,
        ctx: Context,
        channel: TextChannel | Thread,
        *,
        user: BeatStarsUser,
    ) -> Message:
        """Add a channel to receive new tracks from a user."""

        query = """
        SELECT COUNT(*)
        FROM monitor.beatstars
        WHERE guild_id = $1
        AND channel_id = ANY($2::BIGINT[])
        """
        records = cast(
            int,
            await self.bot.db.fetchval(
                query,
                ctx.guild.id,
                [
                    _channel.id
                    for _channel in ctx.guild.text_channels + list(ctx.guild.threads)
                ],
            ),
        )
        if records >= 6:
            return await ctx.warn(
                "This server has reached the maximum amount of monitored users"
            )

        await self.bot.db.execute(
            """
            INSERT INTO monitor.beatstars (guild_id, channel_id, user_id, username)
            VALUES ($1, $2, $3, $4)
            ON CONFLICT (guild_id, user_id)
            DO UPDATE SET
                channel_id = EXCLUDED.channel_id,
                username = EXCLUDED.username
            """,
            ctx.guild.id,
            channel.id,
            user.id,
            user.username,
        )
        return await ctx.approve(
            f"Now notifying {channel.mention} when {user.hyperlink} posts a track"
        )

    @beatstars.group(
        name="message",
        aliases=("template", "msg"),
        invoke_without_command=True,
    )
    @has_permissions(manage_messages=True, mention_everyone=True)
    async def beatstars_message(
        self,
        ctx: Context,
        channel: Optional[TextChannel | Thread],
        user: Optional[BeatStarsUser],
        *,
        script: Script,
    ) -> Message:
        """Set a custom message to send when a user posts a track.

        The available variables can be found [here](https://egirl.software/variables).
        """

        query = "UPDATE monitor.beatstars SET template = $1 WHERE guild_id = $2"
        if not user:
            await ctx.prompt(
                "You did not provide a BeatStars user",
                "Would you like to set the message for all users?",
            )

            result = await self.bot.db.execute(query, script.template, ctx.guild.id)
            if result == "UPDATE 0":
                return await ctx.warn("No BeatStars users have been modified")

            return await ctx.approve(
                f"Set {vowel(script.format)} track message for all BeatStars users"
            )

        query += " AND user_id = $3"
        result = await self.bot.db.execute(
            query,
            script.template,
            ctx.guild.id,
            user.id,
        )
        if result == "UPDATE 0":
            return await ctx.warn(f"A feed for {user.hyperlink} wasn't found")

        return await ctx.approve(
            f"Set {vowel(script.format)} message for tracks from {user.hyperlink}"
        )

    @beatstars_message.command(name="remove", aliases=("delete", "del", "rm"))
    @has_permissions(manage_messages=True, mention_everyone=True)
    async def beatstars_message_remove(
        self,
        ctx: Context,
        channel: Optional[TextChannel | Thread],
        user: Optional[BeatStarsUser],
    ) -> Message:
        """Remove the custom message for a user or all users."""

        query = "UPDATE monitor.beatstars SET template = NULL WHERE guild_id = $1"
        if not user:
            await ctx.prompt(
                "You did not provide a BeatStars user",
                "Would you like to reset the message for all users?",
            )

            result = await self.bot.db.execute(query, ctx.guild.id)
            if result == "UPDATE 0":
                return await ctx.warn("No BeatStars users have been modified")

            return await ctx.approve("Reset the track message for all BeatStars users")

        query += " AND user_id = $2"
        result = await self.bot.db.execute(query, ctx.guild.id, user.id)
        if result == "UPDATE 0":
            return await ctx.warn(f"A feed for {user.hyperlink} wasn't found")

        return await ctx.approve(f"Reset the message for tracks from {user.hyperlink}")

    @beatstars.command(name="migrate", aliases=("transfer", "move"))
    @has_permissions(manage_channels=True)
    async def beatstars_migrate(
        self,
        ctx: Context,
        old_channel: TextChannel | Thread,
        new_channel: TextChannel | Thread,
    ) -> Message:
        """Move all BeatStars users from one channel to another."""

        query = """
        UPDATE monitor.beatstars
        SET channel_id = $1
        WHERE guild_id = $2
        AND channel_id = $3
        """
        result = await self.bot.db.execute(
            query,
            new_channel.id,
            ctx.guild.id,
            old_channel.id,
        )
        if result == "UPDATE 0":
            return await ctx.warn(
                f"No BeatStars users are being monitored in {old_channel.mention}"
            )

        return await ctx.approve(
            f"Moved all BeatStars users from {old_channel.mention} to {new_channel.mention}"
        )

    @beatstars.command(name="remove", aliases=("delete", "del", "rm"))
    @has_permissions(manage_channels=True)
    async def beatstars_remove(
        self,
        ctx: Context,
        channel: Optional[TextChannel | Thread],
        *,
        user: BeatStarsUser,
    ) -> Message:
        """Remove a user from a channel's feed."""

        result = await self.bot.db.execute(
            """
            DELETE FROM monitor.beatstars
            WHERE guild_id = $1
            AND user_id = $2
            """,
            ctx.guild.id,
            user.id,
        )
        if result == "DELETE 0":
            return await ctx.warn(f"A feed for {user.hyperlink} wasn't found")

        return await ctx.approve(
            f"No longer sending notifications for {user.hyperlink}"
        )

    @beatstars.command(name="list")
    @has_permissions(manage_channels=True)
    async def beatstars_list(self, ctx: Context) -> Message:
        """View all BeatStars users being monitored."""

        query = "SELECT * FROM monitor.beatstars WHERE guild_id = $1"
        records = cast(list[Record], await self.bot.db.fetch(query, ctx.guild.id))
        channels = [
            f"{channel.mention} - [`@{record['username']}`](https://beatstars.com/{record['username']})"
            for record in records
            if (channel := ctx.guild.get_channel_or_thread(record["channel_id"]))
        ]
        if not channels:
            return await ctx.warn("No BeatStars users are being monitored")

        embed = Embed(title="BeatStars Notifications")
        paginator = Paginator(ctx, channels, embed)
        return await paginator.start()

    @group(aliases=("lbox",), invoke_without_command=True)
    async def letterboxd(self, ctx: Context) -> Message:
        """Notify a channel when a user posts a review."""

        return await ctx.send_help(ctx.command)

    @letterboxd.command(name="add", aliases=("watch", "feed"))
    @has_permissions(manage_channels=True)
    async def letterboxd_add(
        self,
        ctx: Context,
        channel: TextChannel | Thread,
        *,
        user: LetterboxdUser,
    ) -> Message:
        """Add a channel to receive new reviews from a user."""

        query = """
        SELECT COUNT(*)
        FROM monitor.letterboxd
        WHERE guild_id = $1
        AND channel_id = ANY($2::BIGINT[])
        """
        records = cast(
            int,
            await self.bot.db.fetchval(
                query,
                ctx.guild.id,
                [
                    _channel.id
                    for _channel in ctx.guild.text_channels + list(ctx.guild.threads)
                ],
            ),
        )
        if records >= 30:
            return await ctx.warn(
                "This server has reached the maximum amount of monitored users"
            )

        await self.bot.db.execute(
            """
            INSERT INTO monitor.letterboxd (guild_id, channel_id, user_id, username)
            VALUES ($1, $2, $3, $4)
            ON CONFLICT (guild_id, user_id)
            DO UPDATE SET
                channel_id = EXCLUDED.channel_id,
                username = EXCLUDED.username
            """,
            ctx.guild.id,
            channel.id,
            user.id,
            user.username,
        )
        return await ctx.approve(
            f"Now notifying {channel.mention} when {user.hyperlink} posts a review"
        )

    @letterboxd.group(
        name="message",
        aliases=("template", "msg"),
        invoke_without_command=True,
    )
    @has_permissions(manage_messages=True, mention_everyone=True)
    async def letterboxd_message(
        self,
        ctx: Context,
        channel: Optional[TextChannel | Thread],
        user: Optional[LetterboxdUser],
        *,
        script: Script,
    ) -> Message:
        """Set a custom message to send when a user posts a review.

        The available variables can be found [here](https://egirl.software/variables).
        """

        query = "UPDATE monitor.letterboxd SET template = $1 WHERE guild_id = $2"
        if not user:
            await ctx.prompt(
                "You did not provide a Letterboxd user",
                "Would you like to set the message for all users?",
            )

            result = await self.bot.db.execute(query, script.template, ctx.guild.id)
            if result == "UPDATE 0":
                return await ctx.warn("No Letterboxd users have been modified")

            return await ctx.approve(
                f"Set {vowel(script.format)} review message for all Letterboxd users"
            )

        query += " AND user_id = $3"
        result = await self.bot.db.execute(
            query,
            script.template,
            ctx.guild.id,
            user.id,
        )
        if result == "UPDATE 0":
            return await ctx.warn(f"A feed for {user.hyperlink} wasn't found")

        return await ctx.approve(
            f"Set {vowel(script.format)} message for reviews from {user.hyperlink}"
        )

    @letterboxd_message.command(name="remove", aliases=("delete", "del", "rm"))
    @has_permissions(manage_messages=True, mention_everyone=True)
    async def letterboxd_message_remove(
        self,
        ctx: Context,
        channel: Optional[TextChannel | Thread],
        user: Optional[LetterboxdUser],
    ) -> Message:
        """Remove the custom message for a user or all users."""

        query = "UPDATE monitor.letterboxd SET template = NULL WHERE guild_id = $1"
        if not user:
            await ctx.prompt(
                "You did not provide a Letterboxd user",
                "Would you like to reset the message for all users?",
            )

            result = await self.bot.db.execute(query, ctx.guild.id)
            if result == "UPDATE 0":
                return await ctx.warn("No Letterboxd users have been modified")

            return await ctx.approve(
                "Reset the review message for all Letterboxd users"
            )

        query += " AND user_id = $2"
        result = await self.bot.db.execute(query, ctx.guild.id, user.id)
        if result == "UPDATE 0":
            return await ctx.warn(f"A feed for {user.hyperlink} wasn't found")

        return await ctx.approve(f"Reset the message for reviews from {user.hyperlink}")

    @letterboxd.command(name="migrate", aliases=("transfer", "move"))
    @has_permissions(manage_channels=True)
    async def letterboxd_migrate(
        self,
        ctx: Context,
        old_channel: TextChannel | Thread,
        new_channel: TextChannel | Thread,
    ) -> Message:
        """Move all Letterboxd users from one channel to another."""

        query = """
        UPDATE monitor.letterboxd
        SET channel_id = $1
        WHERE guild_id = $2
        AND channel_id = $3
        """
        result = await self.bot.db.execute(
            query,
            new_channel.id,
            ctx.guild.id,
            old_channel.id,
        )
        if result == "UPDATE 0":
            return await ctx.warn(
                f"No Letterboxd users are being monitored in {old_channel.mention}"
            )

        return await ctx.approve(
            f"Moved all Letterboxd users from {old_channel.mention} to {new_channel.mention}"
        )

    @letterboxd.command(name="remove", aliases=("delete", "del", "rm"))
    @has_permissions(manage_channels=True)
    async def letterboxd_remove(
        self,
        ctx: Context,
        channel: Optional[TextChannel | Thread],
        *,
        user: LetterboxdUser,
    ) -> Message:
        """Remove a user from a channel's feed."""

        result = await self.bot.db.execute(
            """
            DELETE FROM monitor.letterboxd
            WHERE guild_id = $1
            AND user_id = $2
            """,
            ctx.guild.id,
            user.id,
        )
        if result == "DELETE 0":
            return await ctx.warn(f"A feed for {user.hyperlink} wasn't found")

        return await ctx.approve(
            f"No longer sending notifications for {user.hyperlink}"
        )

    @letterboxd.command(name="list")
    @has_permissions(manage_channels=True)
    async def letterboxd_list(self, ctx: Context) -> Message:
        """View all Letterboxd users being monitored."""

        query = "SELECT * FROM monitor.letterboxd WHERE guild_id = $1"
        records = cast(list[Record], await self.bot.db.fetch(query, ctx.guild.id))
        channels = [
            f"{channel.mention} - [`@{record['username']}`](https://letterboxd.com/{record['username']})"
            for record in records
            if (channel := ctx.guild.get_channel_or_thread(record["channel_id"]))
        ]
        if not channels:
            return await ctx.warn("No Letterboxd users are being monitored")

        embed = Embed(title="Letterboxd Notifications")
        paginator = Paginator(ctx, channels, embed)
        return await paginator.start()

    @group(aliases=("subreddit", "r/"), invoke_without_command=True)
    async def reddit(self, ctx: Context, *, subreddit: Subreddit) -> Message:
        """View a subreddit's information."""

        embed = Embed(
            url=f"https://reddit.com{subreddit.url}",
            title=subreddit.title or subreddit.display_name,
        )
        embed.set_thumbnail(url=subreddit.community_icon)
        for name, value in (
            ("Subscribers", subreddit.subscribers),
            ("Active Users", subreddit.accounts_active),
        ):
            embed.add_field(name=name, value=format(value, ","))

        return await ctx.send(embed=embed)

    @reddit.command(name="add", aliases=("watch", "feed"))
    @has_permissions(manage_channels=True)
    async def reddit_add(
        self,
        ctx: Context,
        channel: TextChannel | Thread,
        *,
        subreddit: Subreddit,
    ) -> Message:
        """Add a channel to receive new submissions from a subreddit."""

        query = """
        SELECT COUNT(*)
        FROM monitor.reddit
        WHERE guild_id = $1
        AND channel_id = ANY($2::BIGINT[])
        """
        records = cast(
            int,
            await self.bot.db.fetchval(
                query,
                ctx.guild.id,
                [
                    _channel.id
                    for _channel in ctx.guild.text_channels + list(ctx.guild.threads)
                ],
            ),
        )
        if records >= 15:
            return await ctx.warn(
                "This server has reached the maximum amount of monitored subreddits"
            )

        await self.bot.db.execute(
            """
            INSERT INTO monitor.reddit (guild_id, channel_id, user_id, username)
            VALUES ($1, $2, $3, $4)
            ON CONFLICT (guild_id, user_id)
            DO UPDATE SET
                channel_id = EXCLUDED.channel_id,
                username = EXCLUDED.username
            """,
            ctx.guild.id,
            channel.id,
            subreddit.display_name.lower(),
            subreddit.display_name,
        )
        return await ctx.approve(
            f"Now notifying {channel.mention} of new submissions from [`r/{subreddit.display_name}`](https://reddit.com{subreddit.url})"
        )

    @reddit.command(name="migrate", aliases=("transfer", "move"))
    @has_permissions(manage_channels=True)
    async def reddit_migrate(
        self,
        ctx: Context,
        old_channel: TextChannel | Thread,
        new_channel: TextChannel | Thread,
    ) -> Message:
        """Move all Reddit users from one channel to another."""

        query = """
        UPDATE monitor.reddit
        SET channel_id = $1
        WHERE guild_id = $2
        AND channel_id = $3
        """
        result = await self.bot.db.execute(
            query,
            new_channel.id,
            ctx.guild.id,
            old_channel.id,
        )
        if result == "UPDATE 0":
            return await ctx.warn(
                f"No Subreddits are being monitored in {old_channel.mention}"
            )

        return await ctx.approve(
            f"Moved all Subreddits from {old_channel.mention} to {new_channel.mention}"
        )

    @reddit.command(name="remove", aliases=("delete", "del", "rm"))
    @has_permissions(manage_channels=True)
    async def reddit_remove(
        self,
        ctx: Context,
        channel: Optional[TextChannel | Thread],
        *,
        subreddit: Subreddit,
    ) -> Message:
        """Remove a subreddit from a channel's feed."""

        result = await self.bot.db.execute(
            """
            DELETE FROM monitor.reddit
            WHERE guild_id = $1
            AND user_id = $2
            """,
            ctx.guild.id,
            subreddit.display_name.lower(),
        )
        if result == "DELETE 0":
            return await ctx.warn(
                f"A feed for [`r/{subreddit.display_name}`](https://reddit.com{subreddit.url}) wasn't found"
            )

        return await ctx.approve(
            f"No longer sending notifications for [`r/{subreddit.display_name}`](https://reddit.com{subreddit.url})"
        )

    @reddit.command(name="list")
    @has_permissions(manage_channels=True)
    async def reddit_list(self, ctx: Context) -> Message:
        """View all Reddit subreddits being monitored."""

        query = "SELECT * FROM monitor.reddit WHERE guild_id = $1"
        records = cast(list[Record], await self.bot.db.fetch(query, ctx.guild.id))
        channels = [
            f"{channel.mention} - [`r/{record['username']}`](https://reddit.com/r/{record['user_id']})"
            for record in records
            if (channel := ctx.guild.get_channel_or_thread(record["channel_id"]))
        ]
        if not channels:
            return await ctx.warn("No Subreddits are being monitored")

        embed = Embed(title="Reddit Notifications")
        paginator = Paginator(ctx, channels, embed)
        return await paginator.start()

    @command(aliases=("dzr",))
    async def deezer(self, ctx: Context, *, query: str) -> Message:
        """Search for a query on Deezer."""

        async with ctx.typing():
            response = await self.bot.session.get(
                URL.build(
                    scheme="https",
                    host="api.deezer.com",
                    path="/search",
                ),
                params={"q": query},
            )
            if not response.ok:
                return await ctx.warn("No response was received from the API")

            data = await response.json()
            if not data.get("data"):
                return await ctx.warn("No results were found for the provided query")

        results = [track["link"] for track in data["data"]]
        paginator = Paginator(ctx, results)
        return await paginator.start()


async def setup(bot: Juno) -> None:
    await bot.add_cog(Social(bot))
