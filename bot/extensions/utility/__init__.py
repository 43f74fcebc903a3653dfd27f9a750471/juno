from __future__ import annotations

import asyncio
from json import dumps
import re
from secrets import token_urlsafe
import string
import unicodedata
from contextlib import suppress
from datetime import datetime
from io import BytesIO
from itertools import groupby
from logging import getLogger
from typing import Annotated, Any, List, Literal, Optional, cast
from urllib.parse import quote_plus
from html2text import html2text as h2t

import discord
import uwuify
from dateutil.tz import gettz
from discord import (
    ActivityType,
    AllowedMentions,
    Embed,
    File,
    Guild,
    HTTPException,
    Invite,
    Member,
    Message,
    PartialInviteGuild,
    Role,
    User,
)
from discord.ext.commands import (
    BucketType,
    Cog,
    Range,
    command,
    cooldown,
    flag,
    group,
    has_permissions,
    max_concurrency,
    parameter,
)
from discord.utils import format_dt, utcnow

from playwright.async_api import Error as PageError
from playwright.async_api import TimeoutError as PageTimeoutError
from shazamio import Shazam as ShazamClient
from shazamio.schemas.models import SongSection as ShazamSongSection
from shazamio.serializers import Serialize as ShazamSerialize
from yarl import URL

from bot.core import Context, Juno, Timer
from bot.shared import (
    Paginator,
    Script,
    codeblock,
    cognitive,
    dominant_color,
    get_spotify_activity,
)
from bot.shared.converters import FlagConverter, PartialAttachment, SafeText
from bot.shared.formatter import human_join, ordinal, plural, shorten
from PIL import Image

from .tmdb import TMDB
from .analysis import Analysis
from .birthday import Birthday
from .coomer import Coomer
from .engines.genius import Genius
from .engines.goodreads import Book
from .engines.leetx import Torrent
from .fortnite import Fortnite
from .giveaway import Giveaway
from .google import Google
from .highlight import Highlight

# from .history.avatars import AvatarHistory
from .history.names import NameHistory
from .history.names import Record as NameRecord
from .reminder import Reminder
from .piston import Piston
from .snipe import Snipe
from .spotify import Spotify
from .sports import Sports
from .media import Media

logger = getLogger("bot.utility")


class ScreenshotFlags(FlagConverter):
    delay: Range[int, 1, 10] = flag(
        description="The amount of seconds to let the page render.",
        default=0,
    )

    full_page: bool = flag(
        description="Whether to take a screenshot of the full page.",
        default=False,
    )

    wait_until: Literal["load", "domcontentloaded", "networkidle"] = flag(
        description="When to consider navigation as complete.",
        default="load",
    )

    click: Optional[str] = flag(
        description="A CSS selector to click before taking the screenshot.",
        aliases=["selector", "element", "button"],
    )


class SubdomainFlags(FlagConverter):
    grep: Optional[str] = flag(
        description="A keyword to filter subdomains by.",
        aliases=["keyword", "word", "search"],
    )


class Utility(
    TMDB,
    Analysis,
    Spotify,
    Highlight,
    Piston,
    Snipe,
    Google,
    Coomer,
    Giveaway,
    Reminder,
    Fortnite,
    NameHistory,
    Birthday,
    Sports,
    Media,
    # AvatarHistory,
    Cog,
):
    shazamio: ShazamClient

    def __init__(self, bot: Juno) -> None:
        self.bot = bot
        self.shazamio = ShazamClient()

    @Cog.listener("on_message_without_command")
    async def voice_transcription(self, ctx: Context) -> Optional[Message]:
        """Automatically transcribe voice messages."""

        if not ctx.message.attachments:
            return

        elif not self.bot.config.api.azure:
            return

        attachment = ctx.message.attachments[0]
        if not attachment.is_voice_message() or (attachment.duration or 0) > 120:
            return

        async with ctx.typing():
            buffer = await attachment.read()
            content_type = attachment.content_type or "audio/ogg"
            text = await cognitive.transcribe_audio(buffer, content_type)

        if text:
            return await ctx.reply(embed=Embed(description=f">>> {text}"))

    @Cog.listener()
    async def on_thread_timer_complete(self, timer: Timer) -> None:
        """Delete temporary threads after a certain duration."""

        guild_id = int(timer.kwargs["guild_id"])
        thread_id = int(timer.kwargs["thread_id"])
        guild = self.bot.get_guild(guild_id)
        if not guild:
            return

        thread = guild.get_thread(thread_id)
        if not thread:
            return

        with suppress(HTTPException):
            await thread.delete(reason="Temporary thread has expired.")

    @Cog.listener("on_message_without_command")
    async def afk_listener(self, ctx: Context) -> Optional[Message]:
        """Check if the author or mentioned user is AFK."""

        query = "DELETE FROM afk WHERE user_id = $1 RETURNING left_at"
        left_at = cast(
            Optional[datetime],
            await self.bot.db.fetchval(query, ctx.author.id),
        )
        if left_at:
            return await ctx.respond(
                f"Welcome back! You went AFK {format_dt(left_at, 'R')}"
            )

        elif len(ctx.message.mentions) == 1:
            user = ctx.message.mentions[0]
            query = "SELECT reason, left_at FROM afk WHERE user_id = $1"
            record = await self.bot.db.fetchrow(query, user.id)
            if record:
                return await ctx.respond(
                    f"{user.mention} is currently AFK: **{record['reason']}** - {format_dt(record['left_at'], 'R')}"
                )

    @command(aliases=("away",))
    async def afk(
        self,
        ctx: Context,
        *,
        reason: Annotated[str, SafeText] = "AFK",
    ) -> None:
        """Set an away status with an optional reason."""

        reason = shorten(reason, 200)
        query = """
        INSERT INTO afk (user_id, reason)
        VALUES ($1, $2) ON CONFLICT (user_id) DO NOTHING
        """
        await self.bot.db.execute(query, ctx.author.id, reason)
        return await ctx.add_check()

    @command(aliases=("recognize",))
    @cooldown(1, 5, BucketType.user)
    async def shazam(
        self,
        ctx: Context,
        attachment: PartialAttachment = parameter(
            default=lambda ctx: PartialAttachment.fallback(ctx, ("audio", "video")),
        ),
    ) -> Message:
        """Recognize a song from an attachment."""

        if attachment.format not in ("audio", "video"):
            return await ctx.warn("The file must be an audio or video format")

        async with ctx.typing():
            try:
                data = await self.shazamio.recognize(attachment.buffer)
            except Exception:
                return await ctx.warn("An error occurred while recognizing the song")

        output = ShazamSerialize.full_track(data)
        if not (track := output.track):
            return await ctx.warn(
                f"No tracks were found from [`{attachment.filename}`](<{attachment.url}>)"
            )

        embed = Embed(
            description=f"> Found [**{track.title}**]({URL(f'https://google.com/search?q={track.title} by {track.subtitle}')}) by **{track.subtitle}**"
        )
        for section in track.sections or []:
            if not isinstance(section, ShazamSongSection):
                continue

            embed.set_image(url=section.meta_pages[-1].image)

        return await ctx.reply(embed=embed)

    @group(aliases=("servericon", "sicon"), invoke_without_command=True)
    async def icon(self, ctx: Context, *, invite: Optional[Invite]) -> Message:
        """View a server's icon if one is present."""

        guild = (
            invite.guild
            if isinstance(invite, Invite)
            and isinstance(invite.guild, PartialInviteGuild)
            else ctx.guild
        )
        if not guild.icon:
            return await ctx.warn("The server doesn't have an icon present")

        embed = Embed(
            title=f"{guild}'s icon",
            description=f"[Click here to download]({guild.icon})",
        )
        embed.set_image(url=guild.icon)

        return await ctx.send(embed=embed)

    @icon.command(name="set", aliases=("change", "update"))
    @has_permissions(manage_guild=True)
    async def icon_set(
        self,
        ctx: Context,
        attachment: PartialAttachment = parameter(
            default=lambda ctx: PartialAttachment.fallback(ctx, ("image",)),
        ),
    ) -> Optional[Message]:
        """Change the server's icon."""

        if attachment.format != "image":
            return await ctx.warn("The file must be an image format")

        elif (
            attachment.extension == "gif" and "ANIMATED_ICON" not in ctx.guild.features
        ):
            return await ctx.warn("The server doesn't have access to animated icons")

        async with ctx.typing():
            await ctx.guild.edit(icon=attachment.buffer)

        return await ctx.add_check()

    @group(aliases=("sbanner",), invoke_without_command=True)
    async def serverbanner(self, ctx: Context, *, invite: Optional[Invite]) -> Message:
        """View a server's banner if one is present."""

        guild = (
            invite.guild
            if isinstance(invite, Invite)
            and isinstance(invite.guild, PartialInviteGuild)
            else ctx.guild
        )
        if not guild.banner:
            return await ctx.warn("The server doesn't have a banner present")

        embed = Embed(
            title=f"{guild}'s banner",
            description=f"[Click here to download]({guild.banner})",
        )
        embed.set_image(url=guild.banner)

        return await ctx.send(embed=embed)

    @serverbanner.command(name="set", aliases=("change", "update"))
    @has_permissions(manage_guild=True)
    async def serverbanner_set(
        self,
        ctx: Context,
        attachment: PartialAttachment = parameter(
            default=lambda ctx: PartialAttachment.fallback(ctx, ("image",)),
        ),
    ) -> Optional[Message]:
        """Change the server's banner."""

        if attachment.format != "image":
            return await ctx.warn("The file must be an image format")

        elif attachment.extension == "gif" and "BANNER" not in ctx.guild.features:
            return await ctx.warn("The server doesn't have access to banners")

        async with ctx.typing():
            await ctx.guild.edit(banner=attachment.buffer)

        return await ctx.add_check()

    @command(aliases=("av", "pfp", "avi"))
    async def avatar(
        self,
        ctx: Context,
        user: Member | User = parameter(default=lambda ctx: ctx.author),
    ) -> Message:
        """View a user's avatar."""

        embed = Embed(
            title=f"{user}'s avatar",
            description=f"[Click here to download]({user.display_avatar})",
        )
        embed.set_image(url=user.display_avatar)

        return await ctx.send(embed=embed)

    @command(
        aliases=(
            "spfp",
            "savi",
            "sav",
        )
    )
    async def serveravatar(
        self,
        ctx: Context,
        member: Member = parameter(default=lambda ctx: ctx.author),
    ) -> Message:
        """View a user's avatar."""

        member = member or ctx.author
        if not member.guild_avatar:
            return await ctx.warn(
                "You don't have a server avatar present"
                if member == ctx.author
                else "The member doesn't have a server avatar present"
            )

        embed = Embed(
            title=f"{member}'s server avatar",
            description=f"[Click here to download]({member.guild_avatar})",
        )
        embed.set_image(url=member.guild_avatar)

        return await ctx.send(embed=embed)

    @command(aliases=("userbanner", "ub"))
    async def banner(
        self,
        ctx: Context,
        user: Member | User = parameter(default=lambda ctx: ctx.author),
    ) -> Message:
        """View a user's banner if one is present."""

        user = await self.bot.fetch_user(user.id)
        if not user.banner:
            return await ctx.warn(
                "You don't have a banner present"
                if user == ctx.author
                else "The user doesn't have a banner present"
            )

        embed = Embed(
            title=f"{user}'s banner",
            description=f"[Click here to download]({user.banner})",
        )
        embed.set_image(url=user.banner)

        return await ctx.send(embed=embed)

    @command(aliases=("ui",))
    async def userinfo(
        self,
        ctx: Context,
        user: Member | User = parameter(default=lambda ctx: ctx.author),
    ) -> Message:
        """View information about a user."""

        embed = Embed(title=f"{user} {user.bot * '(BOT)'}")
        embed.set_thumbnail(url=user.display_avatar)
        embed.add_field(
            name="Created",
            value="\n".join(
                [
                    format_dt(user.created_at, "R"),
                    format_dt(user.created_at, "D"),
                ]
            ),
        )
        if isinstance(user, Member):
            joined_at = user.joined_at or discord.utils.utcnow()
            embed.add_field(
                name="Joined",
                value="\n".join(
                    [
                        format_dt(joined_at, "R"),
                        format_dt(joined_at, "D"),
                    ]
                ),
            )
            if user.premium_since:
                embed.add_field(
                    name="Boosted",
                    value="\n".join(
                        [
                            format_dt(user.premium_since, "R"),
                            format_dt(user.premium_since, "D"),
                        ]
                    ),
                )

            if roles := user.roles[1:]:
                embed.add_field(
                    name="Roles",
                    value=", ".join(role.mention for role in list(reversed(roles))[:5])
                    + (f" (+{len(roles) - 5})" if len(roles) > 5 else ""),
                    inline=False,
                )

            if (voice := user.voice) and voice.channel:
                members = len(voice.channel.members) - 1
                phrase = "Streaming inside" if voice.self_stream else "Inside"
                embed.description = (
                    (embed.description or "")
                    + f"🎙 {phrase} {voice.channel.mention} "
                    + (f"with {plural(members):other}" if members else "by themselves")
                )

            for activity_type, activities in groupby(
                user.activities,
                key=lambda activity: activity.type,
            ):
                activities = list(activities)
                if isinstance(activities[0], discord.Spotify):
                    activity = activities[0]
                    embed.description = (
                        (embed.description or "")
                        + f"\n🎵 Listening to [**{activity.title}**]({activity.track_url}) by **{activity.artists[0]}**"
                    )

                elif isinstance(activities[0], discord.Streaming):
                    embed.description = (
                        (embed.description or "")
                        + "\n🎥 Streaming "
                        + human_join(
                            [
                                f"[**{activity.name}**]({activity.url})"
                                for activity in activities
                                if isinstance(activity, discord.Streaming)
                            ],
                            final="and",
                        )
                    )

                elif activity_type == ActivityType.playing:
                    embed.description = (
                        (embed.description or "")
                        + "\n🎮 Playing "
                        + human_join(
                            [f"**{activity.name}**" for activity in activities],
                            final="and",
                        )
                    )

                elif activity_type == ActivityType.watching:
                    embed.description = (
                        (embed.description or "")
                        + "\n📺 Watching "
                        + human_join(
                            [f"**{activity.name}**" for activity in activities],
                            final="and",
                        )
                    )

                elif activity_type == ActivityType.competing:
                    embed.description = (
                        (embed.description or "")
                        + "\n🏆 Competing in "
                        + human_join(
                            [f"**{activity.name}**" for activity in activities],
                            final="and",
                        )
                    )

        query = "SELECT * FROM name_history WHERE user_id = $1 ORDER BY timestamp DESC"
        records = cast(List[NameRecord], await self.bot.db.fetch(query, user.id))
        names = list(filter(lambda record: not record["is_nickname"], records))
        nicknames = list(filter(lambda record: record["is_nickname"], records))
        if names:
            embed.add_field(
                name="Previous Names",
                value=", ".join(f"`{record['username']}`" for record in names[:15]),
                inline=False,
            )

        if nicknames:
            embed.add_field(
                name="Previous Nicknames",
                value=", ".join(f"`{record['username']}`" for record in nicknames[:15]),
                inline=False,
            )

        if user.mutual_guilds and user.id not in {
            *self.bot.config.owner_ids,
            self.bot.user.id,
        }:
            guilds: List[str] = []
            for guild in user.mutual_guilds:
                member = guild.get_member(user.id)
                if not member:
                    continue

                result = []
                if guild.owner_id == user.id:
                    result.append("`👑`")

                elif member.guild_permissions.administrator:
                    result.append("`🛠️`")

                if member.nick:
                    result.append(f"`{member.nick}` in")

                if guild.vanity_url:
                    result.append(f"[__{guild.name}__]({guild.vanity_url})")
                else:
                    result.append(f"__{guild.name}__")

                result.append(f"[`{guild.id}`]")
                guilds.append(" ".join(result))

            embed.add_field(
                name="Shared Servers",
                value="\n".join(guilds[:15]),
                inline=False,
            )

        return await ctx.send(embed=embed)

    @command(aliases=("sinfo", "si"))
    async def serverinfo(
        self,
        ctx: Context,
        *,
        guild: Guild = parameter(default=lambda ctx: ctx.guild),
    ) -> Message:
        """View information about the server."""

        embed = Embed(
            description=f"{format_dt(guild.created_at)} ({format_dt(guild.created_at, 'R')})"
        )
        embed.set_author(
            name=f"{guild.name} ({guild.id})",
            url=guild.vanity_url,
            icon_url=guild.icon,
        )

        embed.add_field(
            name="Information",
            value=(
                "\n".join(
                    [
                        f"**Owner:** {guild.owner or guild.owner_id}",
                        f"**Verification:** {guild.verification_level.name.title()}",
                        f"**Nitro Boosts:** {guild.premium_subscription_count:,} (`Level {guild.premium_tier}`)",
                    ]
                )
            ),
        )
        embed.add_field(
            name="Statistics",
            value=(
                "\n".join(
                    [
                        f"**Members:** {guild.member_count:,}",
                        f"**Text Channels:** {len(guild.text_channels):,}",
                        f"**Voice Channels:** {len(guild.voice_channels):,}",
                    ]
                )
            ),
        )

        if guild == ctx.guild and (roles := guild.roles[1:]):
            roles = list(reversed(roles))

            embed.add_field(
                name=f"Roles ({len(roles)})",
                value=(
                    ", ".join(role.mention for role in roles[:5])
                    + (f" (+{len(roles) - 5})" if len(roles) > 5 else "")
                ),
                inline=False,
            )

        return await ctx.send(embed=embed)

    @command(aliases=("mc",))
    async def membercount(
        self,
        ctx: Context,
        *,
        guild: Guild = parameter(default=lambda ctx: ctx.guild),
    ) -> Message:
        """View the member count of the server."""

        embed = Embed()
        embed.set_author(name=guild, icon_url=guild.icon)

        humans = list(list(filter(lambda member: not member.bot, guild.members)))
        bots = list(list(filter(lambda member: member.bot, guild.members)))
        for key, value in {
            "Members": len(guild.members),
            "Humans": len(humans),
            "Bots": len(bots),
        }.items():
            embed.add_field(name=key, value=format(value, ","))

        return await ctx.send(embed=embed)

    @command(aliases=("ask", "ai"))
    async def gemini(self, ctx: Context, *, prompt: str) -> Message:
        """Ask gemini a prompt."""

        BASE_URL = URL.build(scheme="https", host="gemini.u14.app")
        async with ctx.typing():
            parts: List[Any] = [{"text": prompt + "\nKeep the response under 2000 characteres"}]

            response = await self.bot.session.post(
                BASE_URL / "api/chat",
                params={"token": token_urlsafe(16)},
                json={
                    "messages": [{"role": "user", "parts": parts}],
                    "model": "gemini-1.5-flash-latest",
                    "generationConfig": {
                        "topP": 0.95,
                        "topK": 64,
                        "temperature": 1,
                        "maxOutputTokens": 8192,
                    },
                    "safety": "none",
                },
            )
            if not response.ok:
                return await ctx.warn("No response was received from the API")

            response = await response.text()
            return await ctx.send(response, allowed_mentions=AllowedMentions.none())

    @command(aliases=("math", "w"))
    async def wolfram(self, ctx: Context, *, expression: str) -> Message:
        """Solve a question with Wolfram Alpha."""

        async with ctx.typing():
            response = await self.bot.session.get(
                URL.build(
                    scheme="https",
                    host="api.wolframalpha.com",
                    path="/v1/result",
                ),
                params={
                    "i": expression,
                    "appid": self.bot.config.api.wolfram,
                },
            )
            if not response.ok:
                return await ctx.warn("No response was received from the API")

            response = await response.read()

        return await ctx.send(response.decode("UTF-8"))

    @command(aliases=("answer", "brain"))
    async def brainly(
        self,
        ctx: Context,
        *,
        attachment: PartialAttachment = parameter(
            default=lambda ctx: PartialAttachment.fallback(ctx, ("image",)),
        ),
    ) -> Message:
        """Retrieve the answer to a question from Brainly."""

        if attachment.format != "image":
            return await ctx.send_help(ctx.command)

        buffer = attachment.buffer
        if attachment.extension != "jpeg":
            image = Image.open(BytesIO(buffer))
            image = image.convert("RGB")
            with BytesIO() as output:
                image.save(output, format="JPEG")
                output.seek(0)
                buffer = output.read()

        async with ctx.typing():
            BRAINLY_HOST = "brainly-frontend-answer-service-mobile-prod.external.social-qa-production.z-dn.net"
            response = await self.bot.session.post(
                URL.build(
                    scheme="https",
                    host=BRAINLY_HOST,
                    path="/api/v1/autopublish/us/search-by-image",
                ),
                data={
                    "request": dumps(
                        {
                            "context": {
                                "supportedTypes": [
                                    "question",
                                    "tbsQuestion",
                                    "answerBotResult",
                                    "mathsolverSolution",
                                ],
                                "imageQuality": {},
                            },
                            "query": {},
                        }
                    ),
                    "image": buffer,
                },
                headers={
                    "Host": BRAINLY_HOST,
                    "Connection": "keep-alive",
                    "X-Api-Key": "e3b33745-06d5-47c1-ba62-25403eca7b2f",
                    "Accept": "text/html5+css",
                    "User-Agent": "iOS-App/4.143.0 Brainly/5756 CFNetwork/1496.0.7 Darwin/23.5.0",
                    "X-B-Token-Long": "zUhASeKd21c2Ch0mlTiRL1m_qTjbwZxuAMKmraSJjRU=",
                    "Accept-Language": "en-US,en;q=0.9",
                },
            )
            if not response.ok:
                return await ctx.warn("No response was received from the API")

            response = await response.json()
            results = sorted(
                [
                    result
                    for result in response.get("results", [])
                    if result["type"] == "question"
                ],
                key=lambda x: x["question"]["answer"]["thanksCount"],
                reverse=True,
            )
            if not results:
                return await ctx.warn("No results were found for this question")

        result = results[0]["question"]
        author = result["answer"]["author"]

        embed = Embed(
            url=f"https://brainly.com/question/{result['id']}",
            title=f"Brainly {result['subject']['name']}",
            description=h2t(result["content"]),
        )
        embed.set_author(
            name=f"{author['nick']} ({author['rank']})",
            url=f"https://brainly.com/app/profile/{author['id']}",
            icon_url=author["avatarUrl"] or "https://i.imgur.com/BuLwsIA.png",
        )

        embed.add_field(name="Answer", value=h2t(result["answer"]["content"]))
        return await ctx.send(embed=embed)

    @command(aliases=("char",))
    async def charinfo(self, ctx: Context, *, characters: str) -> Message:
        """View information about unicode characters."""

        def to_string(char: str):
            digit = f"{ord(char):x}"
            name = unicodedata.name(char, "Name not found.")

            return f"[`\\U{digit:>08}`](http://www.fileformat.info/info/unicode/char/{digit}): {name}"

        unicode = list(map(to_string, characters))
        embed = Embed(title="Character Information")

        paginator = Paginator(ctx, unicode, embed, per_page=5, counter=False)
        return await paginator.start()

    @command()
    async def inrole(self, ctx: Context, *, role: Role) -> Message:
        """View members which have a role."""

        members = [f"{member.mention} [`{member.id}`]" for member in role.members]
        if not members:
            return await ctx.warn("No members have this role")

        paginator = Paginator(ctx, members, embed=Embed(title=f"Members with {role}"))
        return await paginator.start()

    @command(
        name="screenshot",
        aliases=["ss"],
    )
    @max_concurrency(1, BucketType.guild)
    @cooldown(1, 4, BucketType.user)
    async def screenshot(
        self,
        ctx: Context,
        url: Annotated[str | URL, str],
        *,
        flags: ScreenshotFlags,
    ) -> Message:
        """Take a screenshot of a website."""

        assert (
            self.bot.browser.context and self.bot.browser.browser
        ), "Browser context is not initialized"
        if not isinstance(url, URL):
            if not url.startswith("http"):
                url = "https://" + url

            url = URL(url)

        await ctx.respond(f"Browser page borrowed for [`{url.host}`]({url})")
        async with ctx.typing():
            if ctx.author.id in self.bot.config.owner_ids:
                page = await self.bot.browser.context.new_page()
            else:
                page = await self.bot.browser.browser.new_page(color_scheme="dark")
            
            await page.set_viewport_size({"width": 1920, "height": 1080})
            try:
                await page.goto(str(url), timeout=15e3, wait_until=flags.wait_until)
            except (PageError, PageTimeoutError):
                await page.close()
                return await ctx.warn(f"Host [`{url.host}`]({url}) is not reachable", edit_response=True)

            if flags.delay:
                await ctx.respond(f"Waiting {flags.delay} seconds for [`{url.host}`]({url})", edit_response=True)
                await asyncio.sleep(flags.delay)

            if flags.click:
                for selector in flags.click.split("->"):
                    await page.click(selector)

            await ctx.respond(f"Taking screenshot of [`{url.host}`]({url})", edit_response=True)
            buffer = await page.screenshot(full_page=flags.full_page)
            await page.close()

            safe = await cognitive.is_image_safe(buffer)
            if not safe and not ctx.channel.is_nsfw():
                return await ctx.warn(f"The [`{url.host}`]({url}) screenshot isn't safe to post", edit_response=True)

        embed = Embed(description=f"> [*`{url.host}`*]({url})")
        embed.set_image(url="attachment://screenshot.png")
        embed.set_footer(
            text=(
                f"Requested by {ctx.author}"
                + (f" ∙ {delay}s delay" if (delay := flags.delay) else "")
                + (" ∙ Full page" if flags.full_page else "")
            ),
        )

        return await ctx.send(
            embed=embed,
            file=File(
                BytesIO(buffer),
                filename="screenshot.png",
            ),
            edit_response=True,
        )

    @command(aliases=("dom", "hex"))
    async def dominant(
        self,
        ctx: Context,
        attachment: PartialAttachment = parameter(
            default=lambda ctx: PartialAttachment.fallback(ctx, ("image",)),
        ),
    ) -> Message:
        """
        Extract the dominant color from an image.
        """

        if attachment.format != "image":
            return await ctx.warn("The file must be an image format")

        async with ctx.typing():
            color = await dominant_color(attachment.buffer)
            response = await self.bot.session.get(
                URL.build(
                    scheme="https",
                    host="api.alexflipnote.dev",
                    path=f"/color/{str(color).strip('#')}",
                ),
            )
            if not response.ok:
                return await ctx.warn("No response was received from the API")

            data = await response.json()

        embed = Embed(
            color=color,
            title=data["name"],
            url=data["images"]["square"],
        )
        embed.set_image(url=data["images"]["gradient"])
        embed.add_field(
            name="HEX",
            value=f"`{data['hex']['string'].upper()}`",
        )
        for key in ("rgb", "hsl"):
            embed.add_field(
                name=key.upper(),
                value=", ".join(map(lambda x: f"`{x}`", data[key]["values"])),
            )

        return await ctx.send(embed=embed)

    # @command(aliases=("optical",))
    # @cooldown(1, 5, BucketType.user)
    # async def ocr(
    #     self,
    #     ctx: Context,
    #     attachment: PartialAttachment = parameter(
    #         default=lambda ctx: PartialAttachment.fallback(ctx, ("image",)
    #     ),
    # ) -> Message:
    #     """Extract text from an image using OCR."""

    #     if attachment.format != "image":
    #         return await ctx.warn("The file must be an image format")

    #     async with ctx.typing():
    #         text = await cognitive.extract_text(ctx.id, attachment.buffer)
    #         if not text:
    #             return await ctx.warn("No text was found in the image")

    #     return await ctx.reply(text, allowed_mentions=AllowedMentions.none())

    # @group(
    #     name="tts",
    #     aliases=(
    #         "speach",
    #         "speak",
    #     ),
    #     invoke_without_command=True,
    # )
    # @cooldown(1, 2, BucketType.user)
    # async def tts(
    #     self,
    #     ctx: Context,
    #     voice: Optional[cognitive.AzureVoice],
    #     *,
    #     text: str,
    # ) -> Message:
    #     """Synthesize text to speech.

    #     Available Voices:
    #     > `Ana`, `Amber`, `Aaria`, `Ashley`, `Brandon`
    #     > `Christopher`, `Cora`, `Davis`, `Elizabeth`,
    #     > `Eric`, `Jacob`, `Jane`, `Jason`, `Jenny`,
    #     > `Michelle`, `Monica`, `Nancy`, `Sara`,
    #     > `Tony`, `Maisie`, `Abbi`, `Molly`
    #     """

    #     async with ctx.typing():
    #         buffer = await cognitive.synthesize_speech(text, voice or "ana")

    #     return await ctx.send(
    #         file=File(
    #             fp=BytesIO(buffer),
    #             filename="speech.ogg",
    #         ),
    #     )

    # @tts.command(name="channel", aliases=("vc", "ch"))
    # async def tts_channel(
    #     self,
    #     ctx: Context,
    #     voice: Optional[cognitive.AzureVoice],
    #     *,
    #     text: str,
    # ) -> Optional[Message]:
    #     """Synthesize text to speech in a voice channel."""

    #     if not ctx.author.voice:
    #         return await ctx.warn("You're not in a voice channel")

    #     command = self.bot.get_command("play")
    #     if not command:
    #         return await ctx.warn("The audio cog is not loaded")

    #     async with temp_file("ogg") as file:
    #         buffer = await cognitive.synthesize_speech(text, "ana")
    #         await file.write_bytes(buffer)

    #         message = copy(ctx.message)
    #         message.content = f"{ctx.prefix}play local.{file.name}"
    #         self.bot.dispatch("message", message)

    #         await ctx.message.add_reaction("🗣")
    #         await asyncio.sleep(10)

    @command(aliases=("parse", "ce", "script"))
    async def embed(self, ctx: Context, *, script: Script) -> Message:
        """Create an embed from a script.

        Learn more about scripting [**here**](https://docs.egirl.software/resources/embed-scripting)."""

        if not ctx.channel.permissions_for(ctx.author).embed_links:
            return await ctx.warn("You don't have permission to embed links")

        try:
            return await script.send(ctx)
        except HTTPException as exc:
            return await ctx.warn(
                "Something is wrong with your script",
                codeblock(exc.text),
            )

    @command(aliases=("embedcode", "ec", "code"))
    async def copyembed(self, ctx: Context, message: Optional[Message]) -> Message:
        """Copy the script template of a message."""

        message = message or ctx.replied_message
        if not message:
            return await ctx.send_help(ctx.command)

        script = Script.from_message(message)
        if not script:
            return await ctx.warn(
                f"That [`message`]({message.jump_url}) doesn't have any content"
            )

        return await ctx.reply(codeblock(script.template, "yaml"))
    
    @command(aliases=("wttr",))
    async def weather(self, ctx: Context, *, location: str) -> Message:
        """View the weather forecast for a location."""

        async with ctx.typing(), self.bot.session.get(
            URL.build(
                scheme="https",
                host="wttr.in",
                path=f"/{quote_plus(location)}",
            ),
            params={"format": "j1"},
        ) as response:
            if not response.ok:
                return await ctx.warn("The provided location wasn't found")

            data = await response.json()
            current = data["current_condition"][0]

        embed = Embed(
            title=(
                current['weatherDesc'][0]['value']
                + f" in {data['nearest_area'][0]['areaName'][0]['value']}"
                + f", {data['nearest_area'][0]['region'][0]['value']}"
            ),
        )
        embed.add_field(
            name="Temperature",
            value=f"{current['temp_F']}°F / {current['temp_C']}°C",
        )
        embed.add_field(
            name="Visibility",
            value=f"{current['visibilityMiles']} miles / {current['visibility']} km",
        )
        embed.add_field(
            name="Wind Speed",
            value=f"{current['windspeedMiles']} mph / {current['windspeedKmph']} km/h",
        )
        embed.add_field(
            name="Humidity",
            value=f"{current['humidity']}%",
        )
        embed.add_field(
            name="Sunrise",
            value=format_dt(
                datetime.strptime(data["weather"][0]["astronomy"][0]["sunrise"], "%I:%M %p"),
                "t",
            ),
        )
        embed.add_field(
            name="Sunset",
            value=format_dt(
                datetime.strptime(data["weather"][0]["astronomy"][0]["sunset"], "%I:%M %p"),
                "t",
            ),
        )
        embed.add_field(
            name="Forecast",
            value="\n".join([
                f"[`{date.strftime('%a').upper()} {ordinal(date.strftime('%d'), 2).upper()}`](https://wttr.in/{quote_plus(location)}) "
                f"{forecast['hourly'][0]['weatherDesc'][0]['value'].strip()}, "
                f"HIGH {forecast['maxtempF']}°F / LOW {forecast['mintempF']}°F"
                for forecast in data["weather"]
                if (date := datetime.strptime(forecast["date"], "%Y-%m-%d"))
            ]),
        )

        return await ctx.send(embed=embed)
    
    @command(aliases=("dictionary", "define", "urban", "ud"))
    async def urbandictionary(self, ctx: Context, *, word: str) -> Message:
        """Define a word with Urban Dictionary."""

        async with ctx.typing():
            response = await self.bot.session.get(
                URL.build(
                    scheme="https",
                    host="api.urbandictionary.com",
                    path="/v0/define",
                    query={"term": word},
                ),
            )
            data = await response.json()
            if not data["list"]:
                return await ctx.warn(f"No definitions exist for **{word}**")

        embeds: List[Embed] = []
        for result in data["list"]:
            embed = Embed(
                url=result["permalink"],
                title=result["word"],
                description=re.sub(
                    r"\[(.*?)\]",
                    lambda m: f"[{m[1]}](https://www.urbandictionary.com/define.php?term={quote_plus(m[1])})",
                    result["definition"],
                )[:4096],
            )

            embed.add_field(
                name="Example",
                value=re.sub(
                    r"\[(.*?)\]",
                    lambda m: f"[{m[1]}](https://www.urbandictionary.com/define.php?term={quote_plus(m[1])})",
                    result["example"],
                )[:1024],
            )
            embeds.append(embed)

        paginator = Paginator(ctx, embeds)
        return await paginator.start()

    @command(hidden=True)
    async def advice(self, ctx: Context) -> Message:
        """Get a random piece of advice."""

        async with ctx.typing():
            response = await self.bot.session.get(
                URL.build(
                    scheme="https",
                    host="api.adviceslip.com",
                    path="/advice",
                ),
            )
            data = await response.json(content_type=None)
            if not data["slip"]:
                return await ctx.warn("No advice was found")

        return await ctx.reply(data["slip"]["advice"])

    @command(aliases=("dadjoke",), hidden=True)
    async def joke(self, ctx: Context) -> Message:
        """Get a random dad joke."""

        async with ctx.typing():
            response = await self.bot.session.get(
                URL.build(
                    scheme="https",
                    host="icanhazdadjoke.com",
                    path="/slack",
                ),
            )
            data = await response.json()
            if not data["attachments"]:
                return await ctx.warn("No jokes were found")

        return await ctx.reply(data["attachments"][0]["text"])

    @command(aliases=("fact",), hidden=True)
    async def trivia(self, ctx: Context) -> Message:
        """Get a random piece of trivia."""

        async with ctx.typing():
            response = await self.bot.session.get(
                URL.build(
                    scheme="https",
                    host="uselessfacts.jsph.pl",
                    path="/random.json",
                ),
            )
            data = await response.json()
            if not data["text"]:
                return await ctx.warn("No trivia was found")

        return await ctx.reply(data["text"])

    @command(aliases=("goodreads", "gr"))
    async def book(self, ctx: Context, *, query: str) -> Message:
        """Search for a book on Goodreads."""

        async with ctx.typing():
            book = await Book.search(query)
            if not book:
                return await ctx.warn(f"No books were found for **{query}**")

        embed = Embed(
            url=book.url,
            title=book.title,
            description=shorten(book.description, 400),
        )
        embed.set_author(
            name=book.author.name,
            url=book.author.url,
            icon_url=book.author.avatar_url,
        )
        embed.set_thumbnail(url=book.cover_url)
        embed.add_field(
            name=f"Rating ({book.rating})",
            value="\n".join(
                [
                    f"{book.ratings:,} ratings",
                    f"{book.reviews:,} reviews",
                ]
            ),
        )
        embed.add_field(name="Pages", value=book.pages.replace(", ", "\n"))
        embed.add_field(
            name="Published",
            value="\n".join(
                [
                    format_dt(book.published, "R"),
                    format_dt(book.published, "D"),
                ]
            ),
        )

        return await ctx.send(embed=embed)

    @command(aliases=("1337x", "torrents"))
    async def torrent(self, ctx: Context, *, query: str) -> Message:
        """Search for torrents on 1337x."""

        async with ctx.typing():
            torrents = await Torrent.search(query)
            if not torrents:
                return await ctx.warn(f"No torrents were found for **{query}**")

        embeds: List[Embed] = []
        for torrent in sorted(
            torrents,
            key=lambda x: x.uploaded_at or datetime.now(),
            reverse=True,
        ):
            embed = Embed(
                url=torrent.url,
                title=f"{shorten(torrent.title, 26)} ({torrent.size})",
            )
            if torrent.uploaded_at:
                embed.description = f"Uploaded {format_dt(torrent.uploaded_at, 'R')} ({format_dt(torrent.uploaded_at, 'D')})"

            embed.add_field(name="Uploader", value=torrent.uploader)
            embed.add_field(name="Seeders", value=torrent.seeders)
            embed.add_field(name="Leechers", value=torrent.leechers)
            embeds.append(embed)

        paginator = Paginator(ctx, embeds)
        return await paginator.start(delete_after=160)

    @command(aliases=("lyric", "lyr", "ly", "genius"))
    async def lyrics(
        self,
        ctx: Context,
        *,
        query: str = parameter(default=get_spotify_activity),
    ) -> Message:
        """Search for lyrics on Genius."""

        async with ctx.typing():
            result = await Genius.search(query)
            if not result:
                return await ctx.warn(f"No lyrics were found for **{query}**")

        embed = Embed(url=result.url, title=result.title)
        embed.set_author(url=result.artist.url, name=result.artist.name)
        embed.set_thumbnail(url=result.thumbnail_url)
        if result.producers:
            embed.set_footer(text=f"Produced by {' & '.join(result.producers)}")

        paginator = Paginator(
            ctx,
            result.lyrics.split("\n"),
            embed,
            per_page=15,
            counter=False,
        )
        return await paginator.start()

    @command(aliases=("uwuify", "uwu"))
    async def uwufy(self, ctx: Context, *, text: Annotated[str, SafeText]) -> Message:
        """Uwufy some text..? -_-"""

        if not text.endswith(string.punctuation):
            text += "."

        flags = uwuify.SMILEY | uwuify.STUTTER | uwuify.YU
        converted = cast(str, uwuify.uwu(text, flags=flags))

        return await ctx.send(
            content=converted,
            allowed_mentions=AllowedMentions.none(),
        )

    @group(aliases=("time", "tz"), invoke_without_command=True)
    async def timezone(
        self,
        ctx: Context,
        *,
        member: Member = parameter(default=lambda ctx: ctx.author),
    ) -> Message:
        """View your or another member's timezone."""

        query = "SELECT timezone_id FROM timezone WHERE user_id = $1"
        timezone = cast(Optional[str], await self.bot.db.fetchval(query, member.id))
        if not timezone:
            if member == ctx.author:
                return await ctx.warn(
                    "You have not set your timezone yet",
                    f"Use `{ctx.clean_prefix}timezone set <location>` to set it",
                )

            return await ctx.warn(f"{member.mention} hasn't set their timezone yet")

        timestamp = utcnow().astimezone(gettz(timezone))
        return await ctx.respond(
            f"It's currently `{timestamp:%B %d, %I:%M %p}` "
            + ("for you" if member == ctx.author else f"for {member.mention}")
        )

    @timezone.command(name="set")
    async def timezone_set(self, ctx: Context, *, location: str) -> Message:
        """Set your timezone."""

        async with ctx.typing():
            response = await self.bot.session.get(
                URL.build(
                    scheme="https",
                    host="api.weatherapi.com",
                    path="/v1/timezone.json",
                ),
                params={
                    "q": location.lower(),
                    "key": self.bot.config.api.weather,
                },
            )
            if not response.ok:
                return await ctx.warn("The provided location could not be resolved")

            data = await response.json()
            timezone = data["location"]["tz_id"]

        query = """
        INSERT INTO timezone (user_id, timezone_id)
        VALUES ($1, $2) ON CONFLICT (user_id)
        DO UPDATE SET timezone_id = EXCLUDED.timezone_id
        """
        await self.bot.db.execute(query, ctx.author.id, timezone)
        return await ctx.approve(f"Your timezone has been set to `{timezone}`")

    @command(aliases=("firstmsg",))
    async def firstmessage(self, ctx: Context) -> Message:
        """View the first message in the current channel."""

        message = [
            message async for message in ctx.channel.history(limit=1, oldest_first=True)
        ][0]
        return await ctx.reply(message.jump_url)

async def setup(bot: Juno) -> None:
    await bot.add_cog(Utility(bot))
