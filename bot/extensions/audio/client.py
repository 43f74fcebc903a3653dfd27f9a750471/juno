import asyncio
from contextlib import suppress
from typing import Any, List, Optional, Self, TypedDict, cast

from cashews import cache
from discord import Guild, HTTPException, Member, Message
from discord.ext.commands import CommandError
from wavelink import AutoPlayMode
from wavelink import Playable as Track
from wavelink import Player
from yarl import URL

from bot.core import Context, Juno
from bot.extensions.lastfm import Lastfm


class LastfmRecord(TypedDict):
    user_id: int
    session_key: str


class Client(Player):
    bot: Juno
    guild: Guild
    message: Optional[Message]
    context: Optional[Context]
    history: List[Track]

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.bot = self.client  # type: ignore
        self.autoplay = AutoPlayMode.disabled
        self.message = None
        self.context = None
        self.history = []

    @classmethod
    async def from_context(cls, ctx: Context) -> Self:
        client = ctx.voice_client
        author = ctx.author

        if not author.voice or not author.voice.channel:
            raise CommandError("You're not in a voice channel")

        elif client and client.channel != author.voice.channel:
            raise CommandError("You're not in my voice channel")

        elif not client:
            if not ctx.command.qualified_name.startswith("play"):
                raise CommandError("I'm not in a voice channel")

            elif not author.voice.channel.permissions_for(ctx.guild.me).connect:
                raise CommandError(
                    "I'm missing permission to connect to your voice channel"
                )

            client = await author.voice.channel.connect(cls=Client, self_deaf=False)
            client.context = ctx
            await client.set_volume(50)

        return cast(Self, client)

    @cache(ttl="30s")
    async def deserialize(self, query: str) -> str:
        response = await self.bot.session.post(
            URL.build(
                scheme="https",
                host="metadata-filter.vercel.app",
                path="/api/youtube",
                query={"track": query},
            ),
        )
        with suppress(Exception):
            data = await response.json()
            return data["data"]["track"]

        return query

    async def scrobble(self, lavalink_track: Track) -> List[Member]:
        lastfm = cast(Optional[Lastfm], self.bot.get_cog("Last.fm"))
        if not lastfm:
            return []

        records = cast(
            List[LastfmRecord],
            await self.bot.db.fetch(
                "SELECT * FROM lastfm.config WHERE user_id = ANY($1::BIGINT[]) AND session_key IS NOT NULL",
                [member.id for member in self.channel.members if not member.bot],
            ),
        )
        if not records:
            return []

        track = await lastfm.client.track.info(
            lavalink_track.title,
            lavalink_track.author,
        )
        listeners: List[Member] = []
        for record in records:
            member = self.guild.get_member(record["user_id"])
            if not member:
                continue

            listeners.append(member)

        await asyncio.gather(
            *[
                lastfm.client.user.scrobble(track, record["session_key"])
                for record in records
            ]
        )
        # this doesn't actually work as intended, it's supposed to run
        # update_now_playing then after half the duration of the track
        # execute the scrobble

        return listeners

    async def disconnect(self, **kwargs: Any) -> None:
        if self.message:
            with suppress(HTTPException):
                await self.message.delete()

        if (reason := kwargs.pop("reason", None)) and self.context:
            with suppress(HTTPException):
                await self.context.channel.send(reason)

        await super().disconnect()
