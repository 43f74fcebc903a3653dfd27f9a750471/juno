from io import BytesIO

from asyncspotify import BadRequest as BadSpotifyRequest
from asyncspotify import Client as SpotifyClient
from asyncspotify import ClientCredentialsFlow as SpotifyClientCredentialsFlow
from cashews import cache
from discord import File, Message
from discord.ext.commands import Cog, group, parameter
from yarl import URL

from bot.core import Context, Juno
from bot.shared import get_spotify_activity

from .canvas_pb2 import EntityCanvazRequest, EntityCanvazResponse


class Spotify(Cog):
    spotify_client: SpotifyClient

    def __init__(self, bot: Juno) -> None:
        self.bot = bot

    async def cog_load(self) -> None:
        self.spotify_client = SpotifyClient(
            SpotifyClientCredentialsFlow(
                client_id=self.bot.config.api.spotify.client_id,
                client_secret=self.bot.config.api.spotify.client_secret,
            )
        )
        self.bot.loop.create_task(self.spotify_client.authorize())
        return await super().cog_load()

    @cache(ttl="1h")
    async def _get_spotify_token(self) -> str:
        response = await self.bot.session.get(
            URL.build(
                scheme="https",
                host="open.spotify.com",
                path="/get_access_token",
            ),
            params={"reason": "transport"},
        )
        data = await response.json()
        return data["accessToken"]

    @group(aliases=("spot", "sp"), invoke_without_command=True)
    async def spotify(
        self,
        ctx: Context,
        *,
        query: str = parameter(default=get_spotify_activity),
    ) -> Message:
        """Search for a song on Spotify."""

        async with ctx.typing():
            try:
                track = await self.spotify_client.search_track(query)
            except BadSpotifyRequest:
                return await ctx.warn(f"No results were found for **{query}**")

        return await ctx.reply(track.link)

    @spotify.command(name="album", aliases=("al",))
    async def spotify_album(
        self,
        ctx: Context,
        *,
        query: str = parameter(default=get_spotify_activity),
    ) -> Message:
        """Search for an album on Spotify."""

        async with ctx.typing():
            try:
                album = await self.spotify_client.search_album(query)
            except BadSpotifyRequest:
                return await ctx.warn(f"No results were found for **{query}**")

        return await ctx.reply(album.link)

    @spotify.command(name="artist", aliases=("ar",))
    async def spotify_artist(
        self,
        ctx: Context,
        *,
        query: str = parameter(default=get_spotify_activity),
    ) -> Message:
        """Search for an artist on Spotify."""

        async with ctx.typing():
            try:
                artist = await self.spotify_client.search_artist(query)
            except BadSpotifyRequest:
                return await ctx.warn(f"No results were found for **{query}**")

        return await ctx.reply(artist.link)

    @spotify.command(name="playlist", aliases=("pl",))
    async def spotify_playlist(
        self,
        ctx: Context,
        *,
        query: str = parameter(default=get_spotify_activity),
    ) -> Message:
        """Search for a playlist on Spotify."""

        async with ctx.typing():
            try:
                playlist = await self.spotify_client.search_playlist(query)
            except BadSpotifyRequest:
                return await ctx.warn(f"No results were found for **{query}**")

        return await ctx.reply(playlist.link)

    @spotify.command(name="canvas", aliases=("cv",))
    async def spotify_canvas(
        self,
        ctx: Context,
        *,
        query: str = parameter(default=get_spotify_activity),
    ) -> Message:
        """View the canvas of a Spotify track."""

        async with ctx.typing():
            try:
                track = await self.spotify_client.search_track(query)
            except BadSpotifyRequest:
                return await ctx.warn(f"No results were found for **{query}**")

            access_token = await self._get_spotify_token()
            request = EntityCanvazRequest()
            request_entities = request.entities.add()
            request_entities.entity_uri = f"spotify:track:{track.id}"
            response = await self.bot.session.post(
                URL.build(
                    scheme="https",
                    host="gew1-spclient.spotify.com",
                    path="/canvaz-cache/v0/canvases",
                ),
                headers={
                    "Content-Type": "application/x-protobuf",
                    "Authorization": f"Bearer {access_token}",
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/116.0.0.0 Safari/537.36",
                },
                data=request.SerializeToString(),
            )
            parsed = EntityCanvazResponse()
            try:
                parsed.ParseFromString(await response.read())
                if not parsed.canvases:
                    raise Exception
            except Exception:
                return await ctx.warn("No canvas was found for this track")

            canvas = parsed.canvases[0]
            response = await self.bot.session.get(canvas.url)
            buffer = await response.read()
            return await ctx.reply(file=File(BytesIO(buffer), filename="canvas.mp4"))
