from __future__ import annotations

import asyncio
from contextlib import suppress
from logging import getLogger
from typing import Annotated, Literal, Optional, cast

from discord import Embed, Member, Message, Spotify as SpotifyActivity
from discord.ext.commands import BucketType, Cog, MaxConcurrency, Range, command, group, parameter
from wavelink import Filters, LavalinkLoadException, NodeReadyEventPayload
from wavelink import Playable as Track
from wavelink import (
    Playlist,
    Pool,
    QueueMode,
    Search,
    TrackEndEventPayload,
    TrackExceptionEventPayload,
    TrackSource,
    TrackStartEventPayload,
)

from bot.core import Context as OriginalContext
from bot.core import Juno
from bot.core.backend.gateway.interfaces import PartialChannel, PartialMember
from bot.shared import Paginator
from bot.shared.formatter import duration, ordinal, plural, shorten

from .client import Client
from .conversion import Position
from .manager import ServerManager

logger = getLogger("bot.audio")
queue_concurrency = MaxConcurrency(1, per=BucketType.guild, wait=True)


class Context(OriginalContext):
    voice: Client


class Audio(Cog):
    manager: ServerManager

    def __init__(self, bot: Juno) -> None:
        self.bot = bot
        self.manager = ServerManager(bot)

    async def cog_load(self) -> None:
        asyncio.create_task(self.manager.connect())

    async def cog_unload(self) -> None:
        await Pool.close()
        await self.manager._partial_shutdown()

    async def cog_before_invoke(self, ctx: Context) -> None:
        ctx.voice = await Client.from_context(ctx)

    @Cog.listener()
    async def on_wavelink_node_ready(self, payload: NodeReadyEventPayload):
        node = payload.node
        logger.info(
            f"Lavalink node {node.identifier} has successfully {node.status.name.lower()}"
        )
        for client in node.players.values():
            await client._dispatch_voice_update()
            if client.current:
                await client.play(client.current, start=client.position)

    @Cog.listener()
    async def on_wavelink_track_start(self, payload: TrackStartEventPayload):
        client = cast(Client, payload.player)
        track = payload.track
        if not client:
            return

        elif track.source == "local":
            return

        client.history.append(track)
        member = client.guild.get_member(getattr(track.extras, "requester_id", 0))
        if track.source in ("spotify", "applemusic", "deezer"):
            await client.scrobble(track)

        await self.bot.backend.gateway.broadcast(
            client.guild.id,
            {
                "event": "AUDIO:TRACK_START",
                "data": {
                    "track": track.raw_data["info"],
                    "member": PartialMember.parse(member).model_dump(mode="json")
                    if member
                    else None,
                    "channel": PartialChannel.parse(client.channel).model_dump(mode="json"),
                },
            },
        )

    @Cog.listener()
    async def on_wavelink_track_end(self, payload: TrackEndEventPayload):
        client = cast(Client, payload.player)
        if not client:
            return

        if client.queue:
            await client.play(client.queue.get())

    @Cog.listener()
    async def on_wavelink_track_exception(self, payload: TrackExceptionEventPayload):
        assert "message" in payload.exception
        logger.error(
            f"An error occurred while playing {payload.track.title}: {payload.exception['message']}"
        )

    @Cog.listener()
    async def on_wavelink_inactive_player(self, client: Client):
        await client.disconnect(reason="Leaving the voice channel due to inactivity")

    @group(aliases=("q",), invoke_without_command=True)
    async def queue(self, ctx: Context) -> Message:
        """View all songs in the queue."""

        queue = ctx.voice.queue or ctx.voice.auto_queue
        if not ctx.voice.current and not queue:
            return await ctx.warn("No tracks are in the queue")

        embed = Embed(title=f"Queue for {ctx.voice.channel.name}")
        embed.description = ""
        if track := ctx.voice.current:
            embed.description = f"Listening to [**{shorten(track.title)}**]({track.uri}) by **{track.author}** [`{duration(ctx.voice.position)}/{duration(track.length)}`]"

        if len(queue) > 10:
            embed.set_footer(text=format(plural(len(queue)), "track"))

        tracks = [
            f"[**{shorten(track.title)}**]({track.uri}) by **{shorten(track.author)}**"
            for track in queue
        ]
        paginator = Paginator(ctx, tracks, embed)
        return await paginator.start()

    @queue.command(name="clear", aliases=("clean", "reset"))
    async def queue_clear(self, ctx: Context) -> Optional[Message]:
        """Remove all tracks from the queue."""

        queue = ctx.voice.queue
        if not queue:
            return await ctx.warn("No tracks are in the queue")

        queue.clear()
        return await ctx.add_check()

    @queue.command(name="shuffle", aliases=("mix",))
    async def queue_shuffle(self, ctx: Context) -> Optional[Message]:
        """Shuffle the queue."""

        queue = ctx.voice.queue
        if not queue:
            return await ctx.warn("No tracks are in the queue")

        queue.shuffle()
        return await ctx.add_check()

    @queue.command(name="remove", aliases=("del", "rm"))
    async def queue_remove(self, ctx: Context, position: int) -> Message:
        """Remove a track from the queue."""

        queue = ctx.voice.queue
        if not queue:
            return await ctx.warn("No tracks are in the queue")

        elif not 0 < position <= len(queue):
            return await ctx.warn(
                f"Invalid position - must be between `1` and `{len(queue)}`"
            )

        track = queue[position - 1]
        queue.remove(track)

        return await ctx.respond(
            f"Removed [**{shorten(track.title)}**]({track.uri}) from the queue"
        )

    @queue.command(name="move", aliases=("mv",))
    async def queue_move(
        self,
        ctx: Context,
        position: int,
        new_position: int,
    ) -> Message:
        """Move a track in the queue."""

        queue = ctx.voice.queue
        if not queue:
            return await ctx.warn("No tracks are in the queue")

        elif not 0 < position <= len(queue):
            return await ctx.warn(
                f"Invalid position - must be between `1` and `{len(queue)}`"
            )

        elif not 0 < new_position <= len(queue):
            return await ctx.warn(
                f"Invalid new position - must be between `1` and `{len(queue)}`"
            )

        track = queue[position - 1]
        queue.remove(track)
        queue.put_at(new_position - 1, track)

        return await ctx.respond(
            f"Moved [**{shorten(track.title)}**]({track.uri}) to `{ordinal(new_position)}` in the queue"
        )

    @command(aliases=("mix",))
    async def shuffle(self, ctx: Context) -> Optional[Message]:
        """Shuffle the queue."""

        return await self.queue_shuffle(ctx)

    @group(
        aliases=("p",),
        invoke_without_command=True,
        max_concurrency=queue_concurrency,
    )
    async def play(self, ctx: Context, *, query: Optional[str]) -> Optional[Message]:
        """Add a song to the queue."""

        if not query:
            if not ctx.message.attachments:
                return await ctx.send_help(ctx.command)

            query = ctx.message.attachments[0].url

        bump = "bump" in query.lower()
        local = "local." in query.lower()
        query = (
            query.replace("bump", "")
            .replace("local.", "/tmp/juno/")
            .replace(
                "spotify:track:",
                "https://open.spotify.com/track/",
            )
            .strip()
        )

        result: Optional[Search] = None
        with suppress(LavalinkLoadException):
            result = await Track.search(
                query,
                source=TrackSource.YouTube if not local else "", # type: ignore
            )

        if not result:
            return await ctx.warn("That query returned no results")

        if isinstance(result, Playlist):
            for track in result.tracks:
                track.extras = {"requester_id": ctx.author.id}

            await ctx.voice.queue.put_wait(result)
            await ctx.respond(
                f"Added [**{result.name}**]({result.url}) with {plural(len(result.tracks), '`'):track} to the queue"
            )
        else:
            track = result[0]
            track.extras = {"requester_id": ctx.author.id}
            if not bump:
                await ctx.voice.queue.put_wait(track)
            else:
                ctx.voice.queue.put_at(0, track)

            if track.source != "local":
                await ctx.respond(
                    f"Added [**{shorten(track.title)}**]({track.uri}) by **{track.author}** to the queue",
                )

        if not ctx.voice.playing:
            await ctx.voice.play(ctx.voice.queue.get())

    @play.command(name="bump")
    async def play_bump(self, ctx: Context, *, query: str) -> Optional[Message]:
        """Add a track to the front of the queue."""

        return await self.play(ctx, query=f"{query} bump")

    @play.command(name="spotify", aliases=("sp",))
    async def play_spotify(self, ctx: Context, *, member: Member = parameter(default=lambda ctx: ctx.author)) -> Optional[Message]:
        """Queue your Spotify presence."""

        for activity in member.activities:
            if isinstance(activity, SpotifyActivity):
                return await self.play(ctx, query=activity.track_url)
            
        return await ctx.warn(
            "You are not currently listening to Spotify"
            if member == ctx.author
            else f"{member} is not currently listening to Spotify"
        )

    @command(
        aliases=("next", "sk"),
        max_concurrency=queue_concurrency,
    )
    async def skip(self, ctx: Context) -> None:
        """Skip the current track."""

        await ctx.voice.skip(force=True)
        return await ctx.add_check()

    @command(aliases=("prev", "back"))
    async def previous(self, ctx: Context) -> Optional[Message]:
        """Go back to the previous track."""

        if not ctx.voice.history:
            return await ctx.warn("No previous tracks to play")

        track = ctx.voice.history.pop()
        ctx.voice.queue.put_at(0, track)
        await ctx.voice.skip(force=True)
        return await ctx.add_check()

    @command()
    async def pause(self, ctx: Context) -> Optional[Message]:
        """Pause the current track."""

        if not ctx.voice.playing:
            return await ctx.warn("No track is currently playing")

        elif ctx.voice.paused:
            return await ctx.warn("The track is already paused")

        await ctx.voice.pause(True)
        return await ctx.add_check()

    @command()
    async def resume(self, ctx: Context) -> Optional[Message]:
        """Resume the current track."""

        if not ctx.voice.current:
            return await ctx.warn("No track is currently playing")

        elif not ctx.voice.paused:
            return await ctx.warn("The track is not paused")

        await ctx.voice.pause(False)
        return await ctx.add_check()

    @command(aliases=("loop",))
    async def repeat(
        self,
        ctx: Context,
        option: Literal["track", "queue", "off"],
    ) -> None:
        """Set the repeat mode."""

        if option == "track":
            ctx.voice.queue.mode = QueueMode.loop
            return await ctx.message.add_reaction("🔂")

        elif option == "queue":
            ctx.voice.queue.mode = QueueMode.loop_all
            return await ctx.message.add_reaction("🔁")

        ctx.voice.queue.mode = QueueMode.normal
        return await ctx.add_check()

    @command(aliases=("fastforward", "rewind", "ff", "rw"))
    async def seek(self, ctx: Context, position: Annotated[int, Position]) -> Message:
        """Seek to a specific position."""

        if not ctx.voice.playing or not ctx.voice.current:
            return await ctx.warn("No track is currently playing")

        await ctx.voice.seek(position)
        return await ctx.approve(
            f"Seeked to `{duration(position)}` in [**{ctx.voice.current}**]({ctx.voice.current.uri})"
        )

    @command(aliases=("vol",))
    async def volume(
        self,
        ctx: Context,
        volume: Optional[Range[int, 1, 100]],
    ) -> Message:
        """Change the track volume."""

        if not volume:
            return await ctx.respond(f"The volume is currently `{ctx.voice.volume}%`")

        await ctx.voice.set_volume(volume)
        return await ctx.respond(f"Set the volume to `{volume}%`")

    @group(aliases=("filter",))
    async def preset(self, ctx: Context) -> Optional[Message]:
        """Set a filter preset on the track."""

        if not ctx.invoked_subcommand:
            return await ctx.send_help(ctx.command)
        
        ctx.voice._filters = Filters()

    @preset.command(name="bassboost", aliases=("boost", "bass", "bb"))
    async def preset_bassboost(self, ctx: Context) -> None:
        """Boost the bass of the track."""

        bands = [
            {"band": 0, "gain": -0.075},
            {"band": 1, "gain": 0.125},
            {"band": 2, "gain": 0.125},
            {"band": 3, "gain": 0.1},
            {"band": 4, "gain": 0.1},
            {"band": 5, "gain": 0.05},
            {"band": 6, "gain": 0.075},
            {"band": 7, "gain": 0.0},
            {"band": 8, "gain": 0.0},
            {"band": 9, "gain": 0.0},
            {"band": 10, "gain": 0.0},
            {"band": 11, "gain": 0.0},
            {"band": 12, "gain": 0.125},
            {"band": 13, "gain": 0.15},
            {"band": 14, "gain": 0.05},
        ]
        filters = ctx.voice.filters
        filters.equalizer.set(bands=bands) # type: ignore

        await ctx.voice.set_filters(filters, seek=True)
        return await ctx.add_check()
    
    @preset.command(name="nightcore", aliases=("nc",))
    async def preset_nightcore(self, ctx: Context) -> None:
        """Accelerates track playback for nightcore-style music"""

        filters = ctx.voice.filters
        filters.timescale.set(pitch=1.2, speed=1.2, rate=1)

        await ctx.voice.set_filters(filters, seek=True)
        return await ctx.add_check()
    
    @preset.command(name="vaporwave", aliases=("vw",))
    async def preset_vaporwave(self, ctx: Context) -> None:
        """Slows down track playback for vintage-style music"""

        filters = ctx.voice.filters
        filters.timescale.set(pitch=0.5, speed=0.8, rate=1)

        await ctx.voice.set_filters(filters, seek=True)
        return await ctx.add_check()
    
    @preset.command(name="remove")
    async def preset_remove(self, ctx: Context) -> None:
        """Remove all filters from the track."""

        await ctx.voice.set_filters(None, seek=True)
        return await ctx.add_check()
    
    @command(aliases=("stop", "dc"))
    async def disconnect(self, ctx: Context) -> None:
        """Disconnect from the voice channel."""

        await ctx.voice.disconnect()
        return await ctx.add_check()

async def setup(bot: Juno) -> None:
    await bot.add_cog(Audio(bot))
