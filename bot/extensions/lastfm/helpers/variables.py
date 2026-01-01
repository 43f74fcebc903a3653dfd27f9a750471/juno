import asyncio
from typing import Optional
from urllib.parse import quote_plus

from pydantic import BaseModel, Field
from yarl import URL

from bot.core import Context
from bot.shared.formatter import duration

from ..api import LastfmClient
from ..api.user import UserInfo
from ..api.user.models.recent_tracks import TrackItem


class Variables:
    class Lastfm(BaseModel):
        url: str
        name: str
        avatar: str
        scrobbles: int
        artist_crown: str
        artists: int
        albums: int
        tracks: int

        def __str__(self) -> str:
            return self.name

        @property
        def plays(self) -> int:
            return self.scrobbles

    class Artist(BaseModel):
        url: str
        name: str
        image: str = Field("null")
        scrobbles: int = Field(default=0)

        def __str__(self) -> str:
            return self.name

        @property
        def plays(self) -> int:
            return self.scrobbles

        @property
        def lower(self) -> str:
            return self.name.lower()

        @property
        def upper(self) -> str:
            return self.name.upper()

    class Album(BaseModel):
        url: str
        name: str
        cover: str = Field("null")

        def __str__(self) -> str:
            return self.name

        @property
        def lower(self) -> str:
            return self.name.lower()

        @property
        def upper(self) -> str:
            return self.name.upper()

        @property
        def image(self) -> str:
            return self.cover or ""

    class Track(BaseModel):
        url: str
        name: str
        image: str = Field("null")
        scrobbles: int
        spotify_url: Optional[str] = Field("null")
        duration: str = Field("1:57")

        def __str__(self) -> str:
            return self.name

        @property
        def plays(self) -> int:
            return self.scrobbles

        @property
        def lower(self) -> str:
            return self.name.lower()

        @property
        def upper(self) -> str:
            return self.name.upper()

    @classmethod
    async def prepare(
        cls,
        ctx: Context,
        client: LastfmClient,
        user: UserInfo,
        _track: TrackItem,
    ) -> list[Artist | Album | Track | Lastfm]:
        track, artist = await asyncio.gather(
            client.track.info(
                _track.name,
                _track.artist.name,
                username=user.name,
                image_url=_track.image_url,
            ),
            client.artist.info(_track.artist.name, username=user.name),
        )
        if _track.album:
            album = cls.Album(
                url=URL.build(
                    scheme="https",
                    host="www.last.fm",
                    path=f"/music/{quote_plus(artist.name)}/{quote_plus(_track.album.text)}",
                ).human_repr(),
                name=_track.album.text,
                cover=_track.image_url or "null",
            )
        else:
            album = cls.Album(
                url=track.url,
                name=track.name,
                cover=track.image_url or "null",
            )

        return [
            cls.Artist(
                url=artist.url,
                name=artist.name,
                image=artist.image_url or "null",
                scrobbles=artist.plays,
            ),
            album,
            cls.Track(
                url=track.url,
                name=track.name,
                image=track.image_url or "null",
                scrobbles=track.plays,
                spotify_url=_track.spotify.link if _track.spotify else _track.url,  # type: ignore
                duration=duration(_track.spotify.duration.total_seconds(), ms=False)
                if _track.spotify
                else "1:57",  # type: ignore
            ),
            cls.Lastfm(
                url=user.url,
                name=user.name,
                avatar=user.image[-1].text or "null",
                scrobbles=user.scrobbles,
                artists=user.artist_count,
                albums=user.album_count,
                tracks=user.track_count,
                artist_crown="",
            ),
        ]
