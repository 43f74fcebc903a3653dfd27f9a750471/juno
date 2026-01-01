import asyncio
import math
from base64 import b64decode
from contextlib import suppress
from datetime import datetime, timedelta
from io import BytesIO
from logging import getLogger
from math import ceil
from secrets import choice, token_urlsafe
from typing import Annotated, List, Literal, Optional, TypedDict, cast

from aiohttp.web import Request, json_response
from asyncspotify import BadRequest as BadSpotifyRequest
from colors import color
from discord import Embed, File, HTTPException, Member, Message, User
from discord.ext.commands import (
    BucketType,
    Cog,
    CommandError,
    Cooldown,
    CooldownMapping,
    Range,
    command,
    cooldown,
    group,
    max_concurrency,
    parameter,
)
from discord.ui import Button, View
from discord.utils import as_chunks, find, format_dt, utcnow
from yarl import URL

from bot.core import Context as OriginalContext
from bot.core import Juno
from bot.extensions.social import Social
from bot.extensions.utility import Utility
from bot.shared import Paginator, codeblock, quietly_delete
from bot.shared.converters.user import StrictMember
from bot.shared.formatter import (
    duration,
    ordinal,
    plural,
    scramble,
    short_timespan,
    shorten,
)
from bot.shared.script import Script
from bot.shared.stopwatch import Stopwatch

from .api import LastfmClient, Timeframe
from .helpers.constants import BREAKPOINTS
from .helpers.image import pixelate
from .helpers.variables import Variables

logger = getLogger("bot.lastfm")

whoknows_cooldown = CooldownMapping(Cooldown(1, 3), BucketType.member)


class LastfmConfig(TypedDict):
    user_id: int
    username: str
    session_key: Optional[str]
    reactions: List[str]
    last_sync: datetime
    command: Optional[str]
    embed_mode: Literal["default", "minimal", "compact"] | str


class Context(OriginalContext):
    config: LastfmConfig


class Lastfm(Cog, name="Last.fm"):
    client: LastfmClient
    authentication: dict[str, tuple[User | Member, Message]] = {}

    def __init__(self, bot: Juno) -> None:
        self.bot = bot
        self.client = LastfmClient()
        self.authentication = dict()

    async def cog_load(self) -> None:
        try:
            self.bot.backend.router.add_get("/lastfm/exchange", self.exchange)
        except RuntimeError:
            ...

    async def cog_unload(self) -> None:
        await self.client.session.close()

    async def cog_before_invoke(self, ctx: Context) -> None:
        if ctx.command in (self.lastfm, self.lastfm_connect):
            return

        query = "SELECT * FROM lastfm.config WHERE user_id = $1"
        member = ctx.author
        for value in list(ctx.kwargs.values()) + ctx.args:
            if ctx.command == self.lastfm_compare:
                break

            if hasattr(value, "mention"):
                member = value
                break

        config = cast(
            Optional[LastfmConfig],
            await self.bot.db.fetchrow(query, member.id),
        )
        if not config:
            raise CommandError(
                "You haven't linked your Last.fm account yet"
                if member == ctx.author
                else f"{member.display_name} hasn't linked their Last.fm account yet"
            )

        ctx.config = config
        return await super().cog_before_invoke(ctx)

    async def exchange(self, request: Request):
        identifier = request.query.get("i")
        token = request.query.get("token")

        if not identifier or not token:
            return json_response({"error": "Missing identifier or token"}, status=400)

        user, message = self.authentication.pop(identifier, [None, None])
        if not user or not message:
            return json_response({"error": "Invalid identifier"}, status=400)

        data = await self.client.get(
            "auth.getSession",
            token=token,
            key=self.bot.config.api.lastfm.public_key,
        )
        if not data:
            return json_response({"error": "Invalid token"}, status=400)

        username = cast(str, data["session"]["name"])
        session_key = cast(str, data["session"]["key"])
        await self.connect_user(user, username, session_key)
        with suppress(HTTPException):
            embed = Embed(
                description="\n".join(
                    [
                        f"Your Last.fm account has been linked as `{username}`",
                        "It might take a few minutes for your Last.fm library to sync",
                    ]
                )
            )
            await user.send(embed=embed)

        await quietly_delete(message)
        asyncio.create_task(self.index(user, username))
        return json_response(
            {
                "message": f"Your Last.fm account has been linked as `{username}`",
                "notice": "You can now close this window and return to Discord",
            }
        )

    async def connect_user(
        self,
        user: User | Member,
        username: str,
        session_key: Optional[str] = None,
    ) -> None:
        query = """
        INSERT INTO lastfm.config (user_id, username, session_key)
        VALUES ($1, $2, $3) ON CONFLICT (user_id)
        DO UPDATE SET
            username = EXCLUDED.username,
            session_key = EXCLUDED.session_key
        """

        await self.bot.db.execute(query, user.id, username, session_key)

    async def index(self, user: User | Member, username: Optional[str] = None) -> None:
        if not username:
            query = "SELECT username FROM lastfm.config WHERE user_id = $1"
            username = cast(Optional[str], await self.bot.db.fetchval(query, user.id))
            if not username:
                raise ValueError("You haven't linked your Last.fm account yet")

        try:
            data = await self.client.user.info(username)
        except CommandError:
            return

        query = "UPDATE lastfm.config SET last_sync = NOW() WHERE user_id = $1"
        await self.bot.db.execute(query, user.id)

        # SYNC ARTIST LIBRARY
        pages = min(ceil(data.artist_count / 1000), 10)
        logger.info(
            f"Gathering {plural(pages):page} from artist library for {user.name}"
        )
        artists = await asyncio.gather(
            *[
                self.client.user.top_artists(username, limit=1000, page=page + 1)
                for page in range(pages)
            ]
        )
        await self.bot.db.execute(
            """
            DELETE FROM lastfm.artists
            WHERE user_id = $1
            AND NOT artist = ANY($2::CITEXT[])
            """,
            user.id,
            [artist.name for page in artists for artist in page.artists],
        )
        await self.bot.db.executemany(
            """
            INSERT INTO lastfm.artists (
                user_id,
                artist,
                plays
            ) VALUES ($1, $2, $3)
            ON CONFLICT (user_id, artist) DO UPDATE
            SET plays = EXCLUDED.plays
            """,
            [
                (user.id, artist.name, artist.playcount)
                for page in artists
                for artist in page.artists
            ],
        )

        # SYNC ALBUM LIBRARY
        pages = min(ceil(data.album_count / 1000), 10)
        logger.info(
            f"Gathering {plural(pages):page} from album library for {user.name}"
        )
        albums = await asyncio.gather(
            *[
                self.client.user.top_albums(username, limit=1000, page=page + 1)
                for page in range(pages)
            ]
        )
        await self.bot.db.execute(
            """
            DELETE FROM lastfm.albums
            WHERE user_id = $1
            """,
            user.id,
        )
        await self.bot.db.executemany(
            """
            INSERT INTO lastfm.albums (
                user_id,
                album,
                artist,
                plays
            ) VALUES ($1, $2, $3, $4)
            ON CONFLICT (user_id, album, artist) DO UPDATE
            SET plays = EXCLUDED.plays
            """,
            [
                (user.id, album.name, album.artist.name, album.playcount)
                for page in albums
                for album in page.albums
            ],
        )

        # SYNC TRACK LIBRARY
        pages = min(ceil(data.track_count / 1000), 10)
        logger.info(
            f"Gathering {plural(pages):page} from track library for {user.name}"
        )
        tracks = await asyncio.gather(
            *[
                self.client.user.top_tracks(username, limit=1000, page=page + 1)
                for page in range(pages)
            ]
        )
        await self.bot.db.execute(
            """
            DELETE FROM lastfm.tracks
            WHERE user_id = $1
            """,
            user.id,
        )
        await self.bot.db.executemany(
            """
            INSERT INTO lastfm.tracks (
                user_id,
                track,
                artist,
                plays
            ) VALUES ($1, $2, $3, $4)
            ON CONFLICT (user_id, track, artist) DO UPDATE
            SET plays = EXCLUDED.plays
            """,
            [
                (user.id, track.name, track.artist.name, track.playcount)
                for page in tracks
                for track in page.tracks
            ],
        )

    @Cog.listener()
    async def on_message_without_command(self, ctx: Context) -> Optional[Message]:
        if not ctx.message.content:
            return

        query = "SELECT command FROM lastfm.config WHERE user_id = $1"
        command = cast(Optional[str], await self.bot.db.fetchval(query, ctx.author.id))
        if not command:
            return

        message = ctx.message
        if not message.content.split(maxsplit=1)[0].lower() == command.lower():
            return

        member = next(
            (mention for mention in message.mentions if isinstance(mention, Member)),
            ctx.author,
        )

        try:
            await self.cog_before_invoke(ctx)
            return await ctx.invoke(self.nowplaying, member=member)
        except CommandError as exc:
            return await ctx.warn(*exc.args)

    @command(aliases=("now", "np", "fm"))
    @cooldown(2, 5, BucketType.user)
    async def nowplaying(
        self,
        ctx: Context,
        *,
        member: Member = parameter(default=lambda ctx: ctx.author),
    ) -> Message:
        """View what you're listening to on Last.fm."""

        async with ctx.typing():
            session_key = (member == ctx.author) * (ctx.config["session_key"] or "")
            user, recent_tracks = await asyncio.gather(
                self.client.user.info(ctx.config["username"]),
                self.client.user.recent_tracks(
                    ctx.config["username"],
                    sk=session_key,
                    limit=1,
                ),
            )
            if not recent_tracks.tracks:
                return await ctx.warn(f"No recent tracks were found for `{user.name}`")

            track = recent_tracks.tracks[0]
            track.data = track
            if ctx.config["embed_mode"] in ("default", "compact", "minimal"):
                with suppress(CommandError, ValueError):
                    track.data = await self.client.track.info(
                        track.name,
                        track.artist.name,
                        username=user.name,
                    )

        embed = Embed()
        embed.set_author(
            url=user.url,
            name=user.realname or user.name,
            icon_url=user.avatar_url,
        )
        if track.image_url:
            embed.set_thumbnail(url=track.image_url)

        if ctx.config["embed_mode"] == "default":
            embed.add_field(
                name="Track",
                value=f"[{track}]({track.url})",
            )
            embed.add_field(
                name="Artist",
                value=f"[{track.artist}]({track.artist.url})",
            )
            embed.set_footer(
                text=" ∙ ".join(
                    [
                        f"Plays: {getattr(track.data, 'plays', 0):,}",
                        f"Scrobbles: {user.scrobbles:,}",
                        f"Album: {(track.album.text or 'N/A')[:16]}",
                    ]
                )
            )

        elif ctx.config["embed_mode"] == "minimal":
            embed.add_field(
                name="Now Playing",
                value="\n".join(
                    [
                        f"[{track.name}]({track.url})",
                        f"by [{track.artist}]({track.artist.url})",
                    ]
                ),
            )

        elif ctx.config["embed_mode"] == "compact":
            embed.add_field(
                name="Now Playing",
                value="\n".join(
                    [
                        f"[{track.name}]({track.url})",
                        f"by [{track.artist}]({track.artist.url})",
                    ]
                ),
            )
            embed.set_footer(
                text=" ∙ ".join(
                    [
                        f"Plays: {getattr(track.data, 'plays', 0):,}",
                        f"Scrobbles: {user.scrobbles:,}",
                    ],
                )
            )

        else:
            if any(
                key in ctx.config["embed_mode"] for key in ("spotify_url", "duration")
            ):
                utility = cast(Optional[Utility], self.bot.get_cog("Utility"))
                if utility:
                    try:
                        track.spotify = await utility.spotify_client.search_track(
                            f"{track.name} {track.artist.name}"
                        )
                    except BadSpotifyRequest:
                        ...

            variables = await Variables.prepare(ctx, self.client, user, track)
            script = Script(ctx.config["embed_mode"], [member, *variables])
            if not script.embeds:
                return await ctx.warn(
                    "Your custom embed mode doesn't have an embed",
                    f"You can use `{ctx.clean_prefix}lastfm mode default` to reset it",
                )

            embed = script.embeds[0]

        try:
            message = await ctx.send(embed=embed)
        except HTTPException as exc:
            return await ctx.warn(
                "Something is wrong with your embed mode",
                codeblock(exc.text),
            )

        if ctx.config["reactions"] != ["0", "0"]:
            for reaction in ctx.config["reactions"] or ["❤️", "🗑"]:
                await asyncio.sleep(0.50)
                try:
                    await message.add_reaction(reaction)
                except HTTPException as exc:
                    if exc.code == 10014:
                        query = "UPDATE lastfm.config SET reactions = $2 WHERE user_id = $1"
                        await self.bot.db.execute(query, ctx.config["user_id"], [])
                    
                    break

        return message

    @group(aliases=("lfm", "lf"), invoke_without_command=True)
    async def lastfm(self, ctx: Context) -> Message:
        """Interact with your Last.fm account."""

        return await ctx.send_help(ctx.command)

    @lastfm.command(name="connect", aliases=("login", "set"))
    async def lastfm_connect(
        self,
        ctx: Context,
        *,
        username: Optional[str] = None,
    ) -> Message:
        """Link your Last.fm account to the bot."""

        if any(ctx.author == author for author, _ in self.authentication.values()):
            return await ctx.warn("You're already in the process of authenticating")

        if username:
            user = await self.client.user.info(username)
            try:
                await ctx.prompt(
                    f"Would you like to authenticate as `{user.name}`?",
                    "This will allow the bot to scrobble audio you listen to",
                )
            except Exception:
                await self.connect_user(ctx.author, username)
                asyncio.create_task(self.index(ctx.author, username))
                return await ctx.approve(
                    f"Your Last.fm account has been linked as `{username}`",
                    "It might take a few minutes for your library to sync",
                )

        embed = Embed(
            title="Last.fm Authentication",
            description="\n".join(
                [
                    "Click the button below to link your Last.fm account to the bot",
                    "If you don't have a Last.fm account yet, you can create one [here](https://www.last.fm/join)",
                ]
            ),
        )

        identifier = token_urlsafe(32)
        view = View()
        view.add_item(
            Button(
                emoji="🔗",
                label="Authenticate",
                url=URL.build(
                    scheme="https",
                    host="www.last.fm",
                    path="/api/auth/",
                    query={
                        "api_key": self.bot.config.api.lastfm.public_key,
                        "cb": f"{self.bot.config.backend.public_url}/lastfm/exchange"
                        + f"?i={identifier}",
                    },
                ).human_repr(),
            )
        )

        try:
            message = await ctx.author.send(embed=embed, view=view)
        except HTTPException as exc:
            if exc.code == 50007:
                return await ctx.warn(
                    "I couldn't DM you. Please enable DMs and try again."
                )
            
            raise

        self.authentication[identifier] = (ctx.author, message)
        await ctx.message.add_reaction("📩")
        return message

    @lastfm.command(name="sync", aliases=("update", "refresh"))
    @max_concurrency(1, BucketType.user)
    @cooldown(1, 60, BucketType.user)
    async def lastfm_sync(self, ctx: Context) -> Message:
        """Refresh your local Last.fm library."""

        query = "SELECT last_sync FROM lastfm.config WHERE user_id = $1"
        last_sync = cast(datetime, await self.bot.db.fetchval(query, ctx.author.id))
        if utcnow() - last_sync < timedelta(minutes=30):
            return await ctx.warn(
                "You've already synced your library recently, try again later"
            )

        await ctx.respond("Syncing your Last.fm library...")
        async with ctx.typing():
            with Stopwatch() as sw:
                await self.index(ctx.author)

        return await ctx.approve(
            f"Your Last.fm library has been synced in `{sw.elapsed:.2f}s`",
            delete_response=True,
        )

    @lastfm.group(
        name="reactions",
        aliases=("reaction", "reacts", "react"),
        invoke_without_command=True,
    )
    async def lastfm_reactions(
        self,
        ctx: Context,
        upvote: str,
        downvote: str,
    ) -> Message:
        """Set custom voting reactions for the now playing command."""

        try:
            for emoji in (upvote, downvote):
                await ctx.message.add_reaction(emoji)
        except HTTPException:
            return await ctx.warn("The provided emojis aren't able to be used")

        query = "UPDATE lastfm.config SET reactions = $2 WHERE user_id = $1"
        await self.bot.db.execute(query, ctx.author.id, [upvote, downvote])
        return await ctx.approve(
            f"Now using {upvote} and {downvote} for voting reactions"
        )

    @lastfm_reactions.command(name="remove", aliases=("none", "delete", "rm"))
    async def lastfm_reactions_remove(self, ctx: Context) -> Message:
        """Remove the voting reactions for the now playing command."""

        query = "UPDATE lastfm.config SET reactions = $2 WHERE user_id = $1"
        await self.bot.db.execute(query, ctx.author.id, ["0", "0"])
        return await ctx.approve("No longer using voting reactions")

    @lastfm_reactions.command(name="reset", aliases=("clear", "default"))
    async def lastfm_reactions_reset(self, ctx: Context) -> Message:
        """Reset the custom voting reactions for the now playing command."""

        query = "UPDATE lastfm.config SET reactions = $2 WHERE user_id = $1"
        await self.bot.db.execute(query, ctx.author.id, [])
        return await ctx.approve("Your custom voting reactions have been reset")

    @lastfm.group(name="mode", invoke_without_command=True)
    async def lastfm_mode(
        self,
        ctx: Context,
        *,
        style: Literal["default", "minimal", "compact"] | Script,
    ) -> Message:
        """Set a custom embed style for the now playing command."""

        if isinstance(style, Script) and not style.embeds:
            return await ctx.warn(
                "Your custom embed mode doesn't have an embed",
                "This command is restricted to only allow embeds",
            )

        query = "UPDATE lastfm.config SET embed_mode = $2 WHERE user_id = $1"
        await self.bot.db.execute(
            query,
            ctx.author.id,
            (style.template if isinstance(style, Script) else style),
        )

        return await ctx.approve(
            f"Your embed mode has been set to `{style}`"
            if not isinstance(style, Script)
            else "Your custom embed mode has been set"
        )

    @lastfm_mode.command(name="view", aliases=("show", "display", "check"))
    async def lastfm_mode_view(self, ctx: Context) -> Message:
        """View your current embed style for the now playing command."""

        if ctx.config["embed_mode"] in ("default", "compact", "minimal", None):
            return await ctx.respond(
                f"You're using the `{ctx.config['embed_mode']}` embed mode"
            )

        return await ctx.send(
            embed=Embed(
                title="Your custom embed script",
                description=codeblock(ctx.config["embed_mode"], "yaml"),
            ),
        )

    @lastfm_mode.command(name="remove", aliases=("delete", "reset", "del", "rm"))
    async def lastfm_mode_remove(self, ctx: Context) -> Message:
        """Remove the custom embed style for the now playing command."""

        return await self.lastfm_mode(ctx, style="default")

    @lastfm.group(
        name="command",
        aliases=("cmd", "cc"),
        invoke_without_command=True,
    )
    async def lastfm_command(self, ctx: Context, command: Range[str, 1, 12]) -> Message:
        """Set a custom command for the now playing command."""

        query = "UPDATE lastfm.config SET command = $2 WHERE user_id = $1"
        await self.bot.db.execute(query, ctx.author.id, command)

        return await ctx.approve(
            f"Your custom command has been set to `{command}`"
            + (
                f"\nYou don't need to include the prefix `{ctx.clean_prefix}`"
                if ctx.prefix and command.startswith(ctx.prefix)
                else ""
            )
        )

    @lastfm_command.command(name="remove", aliases=("delete", "reset", "del", "rm"))
    async def lastfm_command_remove(self, ctx: Context) -> Message:
        """Remove the custom command for the now playing command."""

        query = "UPDATE lastfm.config SET command = $2 WHERE user_id = $1"
        await self.bot.db.execute(query, ctx.author.id, None)

        return await ctx.approve("Your custom command has been removed")

    @lastfm.command(name="recent", aliases=("history", "recenttracks"))
    async def lastfm_recent(
        self,
        ctx: Context,
        member: Optional[Annotated[Member, StrictMember]] = parameter(
            default=lambda ctx: ctx.author
        ),
        *,
        artist: Optional[str] = None,
    ) -> Message:
        """View your most recent tracks on Last.fm."""

        member = member or ctx.author
        async with ctx.typing():
            recent_tracks = await self.client.user.recent_tracks(
                ctx.config["username"],
                sk=ctx.config["session_key"] if member == ctx.author else None,
            )
            if not recent_tracks.tracks:
                return await ctx.warn("No recent tracks were found")

        tracks = [
            f"[**{shorten(track.name)}**]({track.url}) by **{shorten(track.artist.name)}**"
            for track in recent_tracks.tracks
            if not artist or artist.lower() in track.artist.name.lower()
        ][:100]
        if not tracks:
            return await ctx.warn(f"No recent tracks were found for `{artist}`")

        embed = Embed(
            title=f"{member.display_name}'s recent tracks"
            + (f" by `{artist}`" if artist else "")
        )
        pagiantor = Paginator(ctx, tracks, embed)
        return await pagiantor.start()

    @lastfm.command(name="collage", aliases=("chart", "grid", "cg"))
    @max_concurrency(1, BucketType.user)
    async def lastfm_collage(
        self,
        ctx: Context,
        member: Optional[Annotated[Member, StrictMember]] = parameter(
            default=lambda ctx: ctx.author
        ),
        *,
        timeframe: Timeframe = Timeframe("overall"),
    ) -> Message:
        """Generate a collage of your top albums on Last.fm."""

        member = member or ctx.author
        async with ctx.typing():
            response = await self.bot.session.post(
                "https://generator.musicorumapp.com/generate",
                json={
                    "theme": "grid",
                    "options": {
                        "user": ctx.config["username"],
                        "period": timeframe.period,
                        "top": "albums",
                        "size": 3,
                        "names": False,
                        "playcount": False,
                        "story": False,
                    },
                },
            )
            if not response.ok:
                return await ctx.warn(
                    "Something went wrong while generating your collage"
                )

            data = await response.json()
            buffer = b64decode(data["base64"].split(",", 1)[-1])
            image = BytesIO(buffer)

        embed = Embed(title=f"{member.display_name}'s {timeframe} album collage")
        embed.set_image(url="attachment://collage.png")
        return await ctx.send(embed=embed, file=File(image, "collage.png"))

    @lastfm.command(name="topartists", aliases=("artists", "tar", "ta"))
    async def lastfm_top_artists(
        self,
        ctx: Context,
        member: Optional[
            Annotated[
                Member,
                StrictMember,
            ]
        ] = parameter(default=lambda ctx: ctx.author),
        timeframe: Timeframe = Timeframe("overall"),
    ) -> Message:
        """View your top artists on Last.fm."""

        member = member or ctx.author
        async with ctx.typing():
            top_artists = await self.client.user.top_artists(
                ctx.config["username"],
                period=timeframe.period,
            )
            if not top_artists.artists:
                return await ctx.warn(
                    "No top artists were found"
                    + (f" in the {timeframe}" if timeframe.period != "overall" else "")
                )

        artists = [
            f"[**{shorten(artist.name)}**]({artist.url}) ({plural(artist.playcount):play})"
            for artist in top_artists.artists
        ][:100]

        embed = Embed(title=f"{member.display_name}'s {timeframe} top artists")
        pagiantor = Paginator(ctx, artists, embed)
        return await pagiantor.start()

    @lastfm.command(name="topalbums", aliases=("albums", "tal", "tab"))
    async def lastfm_top_albums(
        self,
        ctx: Context,
        member: Optional[
            Annotated[
                Member,
                StrictMember,
            ]
        ] = parameter(default=lambda ctx: ctx.author),
        timeframe: Timeframe = Timeframe("overall"),
    ) -> Message:
        """View your top albums on Last.fm."""

        member = member or ctx.author
        async with ctx.typing():
            top_albums = await self.client.user.top_albums(
                ctx.config["username"],
                period=timeframe.period,
            )
            if not top_albums.albums:
                return await ctx.warn(
                    "No top albums were found"
                    + (f" in the {timeframe}" if timeframe.period != "overall" else "")
                )

        albums = [
            f"[**{shorten(album.name)}**]({album.url}) by **{shorten(album.artist.name)}** ({plural(album.playcount):play})"
            for album in top_albums.albums
        ][:100]

        embed = Embed(title=f"{member.display_name}'s {timeframe} top albums")
        pagiantor = Paginator(ctx, albums, embed)
        return await pagiantor.start()

    @lastfm.command(name="toptracks", aliases=("tracks", "ttr", "tt"))
    async def lastfm_top_tracks(
        self,
        ctx: Context,
        member: Optional[
            Annotated[
                Member,
                StrictMember,
            ]
        ] = parameter(default=lambda ctx: ctx.author),
        timeframe: Timeframe = Timeframe("overall"),
    ) -> Message:
        """View your top tracks on Last.fm."""

        member = member or ctx.author
        async with ctx.typing():
            top_tracks = await self.client.user.top_tracks(
                ctx.config["username"],
                period=timeframe.period,
            )
            if not top_tracks.tracks:
                return await ctx.warn(
                    "No top tracks were found"
                    + (f" in the {timeframe}" if timeframe.period != "overall" else "")
                )

        tracks = [
            f"[**{shorten(track.name)}**]({track.url}) by **{shorten(track.artist.name)}** ({plural(track.playcount):play})"
            for track in top_tracks.tracks
        ][:100]

        embed = Embed(title=f"{member.display_name}'s {timeframe} top tracks")
        pagiantor = Paginator(ctx, tracks, embed)
        return await pagiantor.start()

    @lastfm.command(name="compare", aliases=("vs", "match", "taste"))
    async def lastfm_compare(self, ctx: Context, *, member: Member) -> Message:
        """Compare your music taste with another member."""

        class Artist(TypedDict):
            artist: str
            author_plays: int
            member_plays: int
            symbol: str

        mutual_artists = cast(
            List[Artist],
            await self.bot.db.fetch(
                """
            SELECT
                author.artist,
                COALESCE(author.plays, 0) AS author_plays,
                COALESCE(member.plays, 0) AS member_plays,
                CASE
                    WHEN author.plays > member.plays THEN '>'
                    WHEN author.plays < member.plays THEN '<'
                    ELSE '='
                END AS symbol
            FROM lastfm.artists AS author
            JOIN lastfm.artists AS member
            ON author.artist = member.artist
            WHERE author.user_id = $1
            AND member.user_id = $2
            ORDER BY author.plays DESC
            """,
                ctx.author.id,
                member.id,
            ),
        )
        if not mutual_artists:
            return await ctx.warn(
                f"You don't share any artists with {member.display_name}"
            )

        largest_library = cast(
            int,
            await self.bot.db.fetchval(
                """
                SELECT GREATEST(
                    (
                        SELECT COUNT(*)
                        FROM lastfm.artists
                        WHERE user_id = $1
                    ),
                    (
                        SELECT COUNT(*)
                        FROM lastfm.artists
                        WHERE user_id = $2
                    )
                )
                """,
                ctx.author.id,
                member.id,
            ),
        )
        artists: List[str] = []
        for artist in mutual_artists:
            name = color(
                format(shorten(artist["artist"], 16), "<17"),
                fg="cyan",
                style="bold",
            )
            symbol = color(
                artist["symbol"],
                fg={"=": "yellow", ">": "green", "<": "red"}[artist["symbol"]],
                style="bold",
            )
            artists.append(
                f"{name} {format(artist['author_plays'], ','):>7} {symbol} {artist['member_plays']:,}"
            )

        embeds: List[Embed] = []
        embed = Embed(title=f"{ctx.author.display_name} vs {member.display_name}")
        for chunk in as_chunks(artists, 10):
            embed = embed.copy()
            embed.description = (
                f"You both share {plural(len(mutual_artists), '**'):mutual artist} "
                f"(`{len(mutual_artists) / largest_library:.2%}`)\n>>> "
                + codeblock("\n".join(chunk), "ansi")
            )
            embeds.append(embed)

        paginator = Paginator(ctx, embeds, counter=False)
        return await paginator.start()

    @lastfm.group(name="plays", invoke_without_command=True)
    async def lastfm_plays(
        self,
        ctx: Context,
        member: Optional[Annotated[Member, StrictMember]] = parameter(
            default=lambda ctx: ctx.author
        ),
        *,
        search: Optional[str] = None,
    ) -> Message:
        """View how many plays you have for an artist."""

        member = member or ctx.author
        async with ctx.typing():
            if not search:
                recent_tracks = await self.client.user.recent_tracks(
                    ctx.config["username"],
                    limit=1,
                    sk=ctx.config["session_key"] if member == ctx.author else None,
                )
                if not recent_tracks.tracks:
                    return await ctx.warn(
                        f"No recent tracks were found for `{ctx.config['username']}`"
                    )

                track = recent_tracks.tracks[0]
                search = track.artist.name

            artist = await self.client.artist.info(
                search,
                username=ctx.config["username"],
            )
            if not artist:
                return await ctx.warn(f"No artist found for **{search}**")

        return await ctx.respond(
            f"You have {plural(artist.plays, '**'):play} for **{artist.name}**"
            if member == ctx.author
            else f"{member.display_name} has {plural(artist.plays, '**'):play} for **{artist.name}**"
        )

    @lastfm_plays.command(name="album", aliases=("al", "a"))
    async def lastfm_plays_album(
        self,
        ctx: Context,
        member: Optional[Annotated[Member, StrictMember]] = parameter(
            default=lambda ctx: ctx.author
        ),
        *,
        search: Optional[str] = None,
    ) -> Message:
        """View how many plays you have for an album."""

        member = member or ctx.author

        class TrackRecord(TypedDict):
            track: str
            plays: int

        async with ctx.typing():
            if not search:
                recent_tracks_response = await self.client.user.recent_tracks(
                    ctx.config["username"],
                    limit=1,
                    sk=ctx.config["session_key"] if member == ctx.author else None,
                )
                if not recent_tracks_response.tracks:
                    return await ctx.warn(
                        f"No recent tracks were found for `{ctx.config['username']}`"
                    )

                recent_track = recent_tracks_response.tracks[0]
                if not recent_track.album:
                    return await ctx.warn(f"No album found for **{recent_track.name}**")

                search = f"{recent_track.album} {recent_track.artist}"

            partial_album = await self.client.album.search(search, limit=1)
            album = await self.client.album.info(
                album=partial_album.name,
                artist=partial_album.artist,
                username=ctx.config["username"],
            )

            response_message = (
                f"You have {plural(album.plays, '**'):play} for **{album.name}** by **{album.artist}**"
                if member == ctx.author
                else f"{member.display_name} has {plural(album.plays, '**'):play} for **{album.name}** by **{album.artist}**"
            )
            utility = cast(Optional[Utility], self.bot.get_cog("Utility"))
            if not utility:
                return await ctx.respond(response_message)

            try:
                simple_album = await utility.spotify_client.search_album(search)
                spotify_album = await utility.spotify_client.get_album(simple_album.id)
            except (BadSpotifyRequest, KeyError):
                return await ctx.respond(response_message)

            track_records = cast(
                List[TrackRecord],
                await self.bot.db.fetch(
                    """
                    SELECT
                        track,
                        plays
                    FROM lastfm.tracks
                    WHERE track = ANY($2::CITEXT[])
                    AND artist = $3
                    AND user_id = $1
                    """,
                    member.id,
                    [track.name for track in spotify_album.tracks],
                    album.artist,
                ),
            )
            if not track_records:
                return await ctx.respond(response_message)

            listened_tracks = [
                track
                for track in spotify_album.tracks
                if any(
                    track.name.lower() == record["track"].lower()
                    for record in track_records
                )
            ]

        embed = Embed(
            title=f"{member.display_name}'s plays for {album.name}",
            timestamp=spotify_album.release_date,
        )
        embed.set_footer(
            text=" ∙ ".join(
                [
                    f"{sum(record['plays'] for record in track_records):,} total plays",
                    f"{short_timespan(sum(track.duration.total_seconds() * record['plays'] for track, record in zip(listened_tracks, track_records)), max_units=2)} listened",
                ]
            )
        )
        if spotify_album.images:
            embed.set_thumbnail(url=spotify_album.images[0].url)

        track_play_details = [
            f"`{duration(track.duration.total_seconds(), ms=False)}` **{track.name}** has {plural(record['plays'], '**'):play}"
            for record in sorted(track_records, key=lambda r: r["plays"], reverse=True)
            if (
                track := find(
                    lambda track: track.name.lower() == record["track"].lower(),
                    spotify_album.tracks,
                )
            )  # type: Optional[SpotifyTrack]
        ]
        paginator = Paginator(ctx, track_play_details, embed, counter=False)
        return await paginator.start()

    @lastfm_plays.command(name="track", aliases=("tr", "t"))
    async def lastfm_plays_track(
        self,
        ctx: Context,
        member: Optional[Annotated[Member, StrictMember]] = parameter(
            default=lambda ctx: ctx.author
        ),
        *,
        search: Optional[str] = None,
    ) -> Message:
        """View how many plays you have for a track."""

        member = member or ctx.author
        async with ctx.typing():
            if not search:
                recent_tracks = await self.client.user.recent_tracks(
                    ctx.config["username"],
                    limit=1,
                    sk=ctx.config["session_key"] if member == ctx.author else None,
                )
                if not recent_tracks.tracks:
                    return await ctx.warn(
                        f"No recent tracks were found for `{ctx.config['username']}`"
                    )

                track = recent_tracks.tracks[0]
                search = f"{track.name} {track.artist}"

            track = await self.client.track.search(search, limit=1)
            track = await self.client.track.info(
                track=track.name,
                artist=track.artist,
                username=ctx.config["username"],
            )

        return await ctx.respond(
            f"You have {plural(track.plays, '**'):play} for **{track.name}** by **{track.artist}**"
            if member == ctx.author
            else f"{member.display_name} has {plural(track.plays, '**'):play} for **{track.name}** by **{track.artist}**"
        )

    @lastfm.command(name="playsalbum", aliases=("playsal",))
    async def lastfm_plays_album_alias(
        self,
        ctx: Context,
        member: Optional[Annotated[Member, StrictMember]] = parameter(
            default=lambda ctx: ctx.author
        ),
        *,
        search: Optional[str] = None,
    ) -> Message:
        """View how many plays you have for an album."""

        return await self.lastfm_plays_album(ctx, member=member, search=search)

    @lastfm.command(name="playstrack", aliases=("playst",))
    async def lastfm_plays_track_alias(
        self,
        ctx: Context,
        member: Optional[Annotated[Member, StrictMember]] = parameter(
            default=lambda ctx: ctx.author
        ),
        *,
        search: Optional[str] = None,
    ) -> Message:
        """View how many plays you have for a track."""

        return await self.lastfm_plays_track(ctx, member=member, search=search)

    @lastfm.command(name="whoknows", aliases=("wk",), cooldown=whoknows_cooldown)
    async def lastfm_whoknows(
        self,
        ctx: Context,
        *,
        search: Optional[str] = None,
    ) -> Message:
        """View the top listeners for an artist."""

        class Record(TypedDict):
            user_id: int
            username: str
            plays: int

        async with ctx.typing():
            if not search:
                recent_tracks = await self.client.user.recent_tracks(
                    ctx.config["username"],
                    limit=1,
                    sk=ctx.config["session_key"],
                )
                if not recent_tracks.tracks:
                    return await ctx.warn(
                        f"No recent tracks were found for `{ctx.config['username']}`"
                    )

                track = recent_tracks.tracks[0]
                search = track.artist.name

            artist = await self.client.artist.info(
                search,
                username=ctx.config["username"],
            )
            if not artist:
                return await ctx.warn(f"No artist found for **{search}**")

            records = cast(
                List[Record],
                await self.bot.db.fetch(
                    """
                    SELECT
                        user_id,
                        (
                            SELECT username
                            FROM lastfm.config
                            WHERE user_id = lastfm.artists.user_id
                        ) AS username,
                        plays
                    FROM lastfm.artists
                    WHERE artist = $1
                    AND user_id = ANY($2::BIGINT[])
                    ORDER BY plays DESC
                    LIMIT 100
                    """,
                    artist.name,
                    list(map(lambda user: user.id, ctx.guild.members)),
                ),
            )
            if not records:
                return await ctx.warn(f"Nobody has listened to `{artist.name}`")

        members: List[str] = []
        for record in records:
            member = ctx.guild.get_member(record["user_id"])
            if not member:
                continue

            rank = len(members) + 1
            md = "__" if member == ctx.author else ""

            members.append(
                f"`{str(rank).zfill(2) if rank > 1 else '👑'}` "
                f"[{md}**{member}**{md}](https://last.fm/user/{record['username']}) "
                f"has {plural(record['plays'], '**'):play}"
            )

        embed = Embed(title=f"Who knows {artist.name}?")
        paginator = Paginator(ctx, members, embed, counter=False)
        return await paginator.start()

    @lastfm.command(
        name="wkalbum",
        aliases=("whoknowsalbum", "wka"),
        cooldown=whoknows_cooldown,
    )
    async def lastfm_whoknows_album(
        self,
        ctx: Context,
        *,
        search: Optional[str] = None,
    ) -> Message:
        """View the top listeners for an album."""

        class Record(TypedDict):
            user_id: int
            username: str
            plays: int

        async with ctx.typing():
            if not search:
                recent_tracks = await self.client.user.recent_tracks(
                    ctx.config["username"],
                    limit=1,
                    sk=ctx.config["session_key"],
                )
                if not recent_tracks.tracks:
                    return await ctx.warn(
                        f"No recent tracks were found for `{ctx.config['username']}`"
                    )

                track = recent_tracks.tracks[0]
                if not track.album:
                    return await ctx.warn(f"No album found for **{track.name}**")

                search = f"{track.album} {track.artist}"

            album = await self.client.album.search(search, limit=1)
            album = await self.client.album.info(
                album=album.name,
                artist=album.artist,
                username=ctx.config["username"],
            )
            if not album:
                return await ctx.warn(f"No album found for **{search}**")

            records = cast(
                List[Record],
                await self.bot.db.fetch(
                    """
                    SELECT
                        user_id,
                        (
                            SELECT username
                            FROM lastfm.config
                            WHERE user_id = lastfm.albums.user_id
                        ) AS username,
                        plays
                    FROM lastfm.albums
                    WHERE album = $1
                    AND artist = $2
                    AND user_id = ANY($3::BIGINT[])
                    ORDER BY plays DESC
                    LIMIT 100
                    """,
                    album.name,
                    album.artist,
                    list(map(lambda user: user.id, ctx.guild.members)),
                ),
            )
            if not records:
                return await ctx.warn(f"Nobody has listened to `{album.name}`")

        members: List[str] = []
        for record in records:
            member = ctx.guild.get_member(record["user_id"])
            if not member:
                continue

            rank = len(members) + 1
            md = "__" if member == ctx.author else ""

            members.append(
                f"`{str(rank).zfill(2) if rank > 1 else '👑'}` "
                f"[{md}**{member}**{md}](https://last.fm/user/{record['username']}) "
                f"has {plural(record['plays'], '**'):play}"
            )

        embed = Embed(title=f"Who knows {album.name}?")
        paginator = Paginator(ctx, members, embed, counter=False)
        return await paginator.start()

    @lastfm.command(
        name="wktrack",
        aliases=("whoknowstrack", "wkt"),
        cooldown=whoknows_cooldown,
    )
    async def lastfm_whoknows_track(
        self,
        ctx: Context,
        *,
        search: Optional[str] = None,
    ) -> Message:
        """View the top listeners for a track."""

        class Record(TypedDict):
            user_id: int
            username: str
            plays: int

        async with ctx.typing():
            if not search:
                recent_tracks = await self.client.user.recent_tracks(
                    ctx.config["username"],
                    limit=1,
                    sk=ctx.config["session_key"],
                )
                if not recent_tracks.tracks:
                    return await ctx.warn(
                        f"No recent tracks were found for `{ctx.config['username']}`"
                    )

                track = recent_tracks.tracks[0]
                search = f"{track.name} {track.artist}"

            track = await self.client.track.search(search, limit=1)
            track = await self.client.track.info(
                track=track.name,
                artist=track.artist,
                username=ctx.config["username"],
            )
            if not track:
                return await ctx.warn(f"No track found for **{search}**")

            records = cast(
                List[Record],
                await self.bot.db.fetch(
                    """
                    SELECT
                        user_id,
                        (
                            SELECT username
                            FROM lastfm.config
                            WHERE user_id = lastfm.tracks.user_id
                        ) AS username,
                        plays
                    FROM lastfm.tracks
                    WHERE track = $1
                    AND artist = $2
                    AND user_id = ANY($3::BIGINT[])
                    ORDER BY plays DESC
                    LIMIT 100
                    """,
                    track.name,
                    track.artist.name,
                    list(map(lambda user: user.id, ctx.guild.members)),
                ),
            )
            if not records:
                return await ctx.warn(f"Nobody has listened to `{track.name}`")

        members: List[str] = []
        for record in records:
            member = ctx.guild.get_member(record["user_id"])
            if not member:
                continue

            rank = len(members) + 1
            md = "__" if member == ctx.author else ""

            members.append(
                f"`{str(rank).zfill(2) if rank > 1 else '👑'}` "
                f"[{md}**{member}**{md}](https://last.fm/user/{record['username']}) "
                f"has {plural(record['plays'], '**'):play}"
            )

        embed = Embed(title=f"Who knows {track.name}?")
        paginator = Paginator(ctx, members, embed, counter=False)
        return await paginator.start()

    @lastfm.command(
        name="globalwhoknows",
        aliases=("globalwk", "gwk"),
        cooldown=whoknows_cooldown,
    )
    async def lastfm_global_whoknows(
        self,
        ctx: Context,
        *,
        search: Optional[str] = None,
    ) -> Message:
        """View the top listeners for an artist globally."""

        class Record(TypedDict):
            user_id: int
            username: str
            plays: int

        async with ctx.typing():
            if not search:
                recent_tracks = await self.client.user.recent_tracks(
                    ctx.config["username"],
                    limit=1,
                    sk=ctx.config["session_key"],
                )
                if not recent_tracks.tracks:
                    return await ctx.warn(
                        f"No recent tracks were found for `{ctx.config['username']}`"
                    )

                track = recent_tracks.tracks[0]
                search = track.artist.name

            artist = await self.client.artist.info(search)
            if not artist:
                return await ctx.warn(f"No artist found for **{search}**")

            records = cast(
                List[Record],
                await self.bot.db.fetch(
                    """
                    SELECT
                        user_id,
                        (
                            SELECT username
                            FROM lastfm.config
                            WHERE user_id = lastfm.artists.user_id
                        ) AS username,
                        plays
                    FROM lastfm.artists
                    WHERE artist = $1
                    ORDER BY plays DESC
                    LIMIT 100
                    """,
                    artist.name,
                ),
            )
            if not records:
                return await ctx.warn(f"Nobody has listened to `{artist.name}`")

        members: List[str] = []
        for record in records:
            user = self.bot.get_user(record["user_id"])
            if not user:
                continue

            rank = len(members) + 1
            md = "__" if user == ctx.author else ""

            members.append(
                f"`{str(rank).zfill(2) if rank > 1 else '👑'}` "
                f"[{md}**{user}**{md}](https://last.fm/user/{record['username']}) "
                f"has {plural(record['plays'], '**'):play}"
            )

        embed = Embed(title=f"Who knows {artist.name} globally?")
        paginator = Paginator(ctx, members, embed, counter=False)
        return await paginator.start()

    @lastfm.command(
        name="globalwkalbum",
        aliases=("globalwka", "gwka"),
        cooldown=whoknows_cooldown,
    )
    async def lastfm_global_whoknows_album(
        self,
        ctx: Context,
        *,
        search: Optional[str] = None,
    ) -> Message:
        """View the top listeners for an album globally."""

        class Record(TypedDict):
            user_id: int
            username: str
            plays: int

        async with ctx.typing():
            if not search:
                recent_tracks = await self.client.user.recent_tracks(
                    ctx.config["username"],
                    limit=1,
                    sk=ctx.config["session_key"],
                )
                if not recent_tracks.tracks:
                    return await ctx.warn(
                        f"No recent tracks were found for `{ctx.config['username']}`"
                    )

                track = recent_tracks.tracks[0]
                if not track.album:
                    return await ctx.warn(f"No album found for **{track.name}**")

                search = f"{track.album} {track.artist}"

            album = await self.client.album.search(search, limit=1)
            album = await self.client.album.info(
                album=album.name,
                artist=album.artist,
            )
            if not album:
                return await ctx.warn(f"No album found for **{search}**")

            records = cast(
                List[Record],
                await self.bot.db.fetch(
                    """
                    SELECT
                        user_id,
                        (
                            SELECT username
                            FROM lastfm.config
                            WHERE user_id = lastfm.albums.user_id
                        ) AS username,
                        plays
                    FROM lastfm.albums
                    WHERE album = $1
                    AND artist = $2
                    ORDER BY plays DESC
                    LIMIT 100
                    """,
                    album.name,
                    album.artist,
                ),
            )
            if not records:
                return await ctx.warn(f"Nobody has listened to `{album.name}`")

        members: List[str] = []
        for record in records:
            user = self.bot.get_user(record["user_id"])
            if not user:
                continue

            rank = len(members) + 1
            md = "__" if user == ctx.author else ""

            members.append(
                f"`{str(rank).zfill(2) if rank > 1 else '👑'}` "
                f"[{md}**{user}**{md}](https://last.fm/user/{record['username']}) "
                f"has {plural(record['plays'], '**'):play}"
            )

        embed = Embed(title=f"Who knows {album.name} globally?")
        paginator = Paginator(ctx, members, embed, counter=False)
        return await paginator.start()

    @lastfm.command(
        name="globalwktrack",
        aliases=("globalwkt", "gwkt"),
        cooldown=whoknows_cooldown,
    )
    async def lastfm_global_whoknows_track(
        self,
        ctx: Context,
        *,
        search: Optional[str] = None,
    ) -> Message:
        """View the top listeners for a track globally."""

        class Record(TypedDict):
            user_id: int
            username: str
            plays: int

        async with ctx.typing():
            if not search:
                recent_tracks = await self.client.user.recent_tracks(
                    ctx.config["username"],
                    limit=1,
                    sk=ctx.config["session_key"],
                )
                if not recent_tracks.tracks:
                    return await ctx.warn(
                        f"No recent tracks were found for `{ctx.config['username']}`"
                    )

                track = recent_tracks.tracks[0]
                search = f"{track.name} {track.artist}"

            track = await self.client.track.search(search, limit=1)
            track = await self.client.track.info(
                track=track.name,
                artist=track.artist,
            )
            if not track:
                return await ctx.warn(f"No track found for **{search}**")

            records = cast(
                List[Record],
                await self.bot.db.fetch(
                    """
                    SELECT
                        user_id,
                        (
                            SELECT username
                            FROM lastfm.config
                            WHERE user_id = lastfm.tracks.user_id
                        ) AS username,
                        plays
                    FROM lastfm.tracks
                    WHERE track = $1
                    AND artist = $2
                    ORDER BY plays DESC
                    LIMIT 100
                    """,
                    track.name,
                    track.artist.name,
                ),
            )
            if not records:
                return await ctx.warn(f"Nobody has listened to `{track.name}`")

        members: List[str] = []
        for record in records:
            user = self.bot.get_user(record["user_id"])
            if not user:
                continue

            rank = len(members) + 1
            md = "__" if user == ctx.author else ""

            members.append(
                f"`{str(rank).zfill(2) if rank > 1 else '👑'}` "
                f"[{md}**{user}**{md}](https://last.fm/user/{record['username']}) "
                f"has {plural(record['plays'], '**'):play}"
            )

        embed = Embed(title=f"Who knows {track.name} globally?")
        paginator = Paginator(ctx, members, embed, counter=False)
        return await paginator.start()

    @lastfm.command(name="pixelate", aliases=("pixel", "jumble", "px"))
    @max_concurrency(1, BucketType.channel)
    async def lastfm_pixelate(self, ctx: Context) -> Message:
        """Try to guess the pixelated album cover."""

        async with ctx.typing():
            top_albums = await self.client.user.top_albums(
                ctx.config["username"],
                limit=100,
                period="overall",
            )
            if not top_albums.albums:
                return await ctx.warn("No top albums were found")

            album = choice(
                [
                    album
                    for album in top_albums.albums
                    if album.image[-1].text and len(album.name) < 16
                ]
            )
            album = await self.client.album.info(
                album=album.name,
                artist=album.artist.name,
                username=ctx.config["username"],
            )
            response = await self.bot.session.get(album.image[-1].text)
            if not response.ok:
                return await self.lastfm_pixelate(ctx)

            cover = await response.read()
            buffer = await pixelate(cover, 30)

        level = 30
        embed = Embed(
            title=f"`{scramble(album.name).upper()}`",
            description="\n".join(
                [
                    "Pixel Jumble - Guess the album",
                    f"> You have {plural(album.plays, '**'):play} for this album",
                ]
            ),
        )
        embed.set_image(url="attachment://pixelated.png")
        message = await ctx.send(embed=embed, file=File(buffer, "pixelated.png"))
        guess: Optional[Message] = None

        def check(m: Message) -> bool:
            return m.channel == ctx.channel and album.name.lower() in m.content.lower()

        with Stopwatch() as sw:
            while sw.elapsed < 40:
                try:
                    guess = await self.bot.wait_for("message", check=check, timeout=5)
                except asyncio.TimeoutError:
                    level -= 10
                    if level == 0:
                        break

                    embed.title = f"`{scramble(album.name).upper()}`"
                    buffer = await pixelate(cover, level)
                    await message.edit(
                        embed=embed,
                        attachments=[File(buffer, "pixelated.png")],
                    )
                    continue

                break

        embed.title = f"`{album.name.upper()}`"
        with suppress(HTTPException):
            await message.edit(
                embed=embed,
                attachments=[File(BytesIO(cover), "pixelated.png")],
            )

        if not guess:
            return await ctx.warn(f"Nobody guessed it right. It was `{album.name}`")

        return await ctx.approve(
            f"**{guess.author.display_name}** got it in `{sw.elapsed:.2f}s`! It was `{album.name}` by {album.artist}"
        )

    @lastfm.command(name="pace")
    async def lastfm_pace(
        self,
        ctx: Context,
        member: Optional[Annotated[Member, StrictMember]] = parameter(
            default=lambda ctx: ctx.author
        ),
        goal: Range[int, 1] = 0,
    ) -> Message:
        """View an estimated date for your next milestone."""

        member = member or ctx.author
        user = await self.client.user.info(ctx.config["username"])
        if goal < user.scrobbles:
            for breakpoint in BREAKPOINTS:
                if user.scrobbles < breakpoint:
                    goal = breakpoint
                    break

        return await ctx.send(
            ("You" if member == ctx.author else f"**{member.display_name}**")
            + f" will reach `{goal:,}` scrobbles on {format_dt(user.milestone_date(goal), 'D')}"
            f"\n-# Estimated by an average of `{user.average:.2f}` scrobbles per day - {user.scrobbles:,} in {(utcnow() - user.registered.date).days} days"
        )

    @lastfm.command(name="milestone", aliases=("ms",))
    @cooldown(3, 60, BucketType.user)
    @max_concurrency(1, BucketType.user)
    async def lastfm_milestone(
        self,
        ctx: Context,
        member: Optional[Annotated[Member, StrictMember]] = parameter(
            default=lambda ctx: ctx.author
        ),
        milestone: Range[int, 1] = 1,
    ) -> Message:
        """View what your scrobble for a milestone was."""

        member = member or ctx.author
        ord_milestone = ordinal(milestone)

        await ctx.typing()
        prep_data = await self.client.user.recent_tracks(
            ctx.config["username"],
            sk=ctx.config["session_key"],
            limit=1000,
        )
        scrobbles = prep_data.field_attr.total
        total_pages = prep_data.field_attr.totalPages
        if milestone > scrobbles:
            return await ctx.warn(
                (
                    "You only have"
                    if member == ctx.author
                    else f"**{member.display_name}** only has"
                )
                + f" `{scrobbles:,}` scrobbles\nUnable to show the `{ord_milestone}` scrobble"
            )

        remainder = scrobbles % 1000
        total_pages = int(prep_data.field_attr.totalPages)

        if milestone > remainder:
            milestone = milestone - remainder
            containing_page = total_pages - math.ceil(milestone / 1000)
        else:
            containing_page = total_pages

        data = await self.client.user.recent_tracks(
            ctx.config["username"],
            sk=ctx.config["session_key"],
            limit=1000,
            page=containing_page,
        )
        tracks = list(reversed(data.tracks))
        track = tracks[(milestone % 1000) - 1]
        timestamp = track.date.timestamp if track.date else None

        return await ctx.respond(
            ("Your" if member == ctx.author else f"**{member.display_name}**'s")
            + f" `{ord_milestone}` scrobble was **{track.name}** by **{track.artist}** {format_dt(timestamp, 'R') if timestamp else ''}",
        )

    @lastfm.command(name="lyrics", aliases=("lyric", "lyr", "ly", "genius"))
    async def lastfm_lyrics(
        self,
        ctx: Context,
        *,
        member: Member = parameter(default=lambda ctx: ctx.author),
    ) -> Message:
        """View the lyrics for your Last.fm track."""

        utility = cast(Optional[Utility], self.bot.get_cog("Utility"))
        if not utility:
            return await ctx.send("This command is not available at the moment")

        async with ctx.typing():
            session_key = (member == ctx.author) * (ctx.config["session_key"] or "")
            recent_tracks = await self.client.user.recent_tracks(
                ctx.config["username"],
                sk=session_key,
                limit=1,
            )
            if not recent_tracks.tracks:
                return await ctx.warn(
                    f"No recent tracks were found for `{ctx.config['username']}`"
                )

            track = recent_tracks.tracks[0]
            return await utility.lyrics(ctx, query=f"{track.name} - {track.artist}")

    @lastfm.command(name="soundcloud", aliases=("sc",))
    async def lastfm_soundcloud(
        self,
        ctx: Context,
        *,
        member: Member = parameter(default=lambda ctx: ctx.author),
    ) -> Message:
        """View your Last.fm track on SoundCloud."""

        social = cast(Optional[Social], self.bot.get_cog("Social"))
        if not social:
            return await ctx.send("This command is not available at the moment")

        async with ctx.typing():
            session_key = (member == ctx.author) * (ctx.config["session_key"] or "")
            recent_tracks = await self.client.user.recent_tracks(
                ctx.config["username"],
                sk=session_key,
                limit=1,
            )
            if not recent_tracks.tracks:
                return await ctx.warn(
                    f"No recent tracks were found for `{ctx.config['username']}`"
                )

            track = recent_tracks.tracks[0]
            return await social.soundcloud(ctx, query=f"{track.name} - {track.artist}")

    @lastfm.command(name="spotify", aliases=("sp",))
    async def lastfm_spotify(
        self,
        ctx: Context,
        *,
        member: Member = parameter(default=lambda ctx: ctx.author),
    ) -> Message:
        """View your Last.fm track on Spotify."""

        utility = cast(Optional[Utility], self.bot.get_cog("Utility"))
        if not utility:
            return await ctx.send("This command is not available at the moment")

        async with ctx.typing():
            session_key = (member == ctx.author) * (ctx.config["session_key"] or "")
            recent_tracks = await self.client.user.recent_tracks(
                ctx.config["username"],
                sk=session_key,
                limit=1,
            )
            if not recent_tracks.tracks:
                return await ctx.warn(
                    f"No recent tracks were found for `{ctx.config['username']}`"
                )

            track = recent_tracks.tracks[0]
            return await utility.spotify(ctx, query=f"{track.name} - {track.artist}")


async def setup(bot: Juno) -> None:
    await bot.add_cog(Lastfm(bot))
