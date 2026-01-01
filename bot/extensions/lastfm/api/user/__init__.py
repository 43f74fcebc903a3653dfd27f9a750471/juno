from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from cashews import cache
from discord.ext.commands import CommandError
from discord.utils import utcnow

from .models import (
    LovedTracks,
    RecentTracks,
    TopAlbums,
    TopArtists,
    TopTracks,
    UserInfo,
)

if TYPE_CHECKING:
    from .. import LastfmClient, Period
    from ..track import TrackInfo


class User:
    client: LastfmClient

    def __init__(self, client: LastfmClient) -> None:
        self.client = client

    @cache(ttl="30m")
    async def info(self, username: str) -> UserInfo:
        data = await self.client.get("user.getInfo", user=username)
        if not data.get("user"):
            raise CommandError(f"Last.fm user `{username}` not found")

        return UserInfo(**data["user"])

    async def loved_tracks(
        self,
        username: str,
        limit: int = 100,
        page: int = 1,
    ) -> LovedTracks:
        data = await self.client.get(
            "user.getLovedTracks",
            user=username,
            limit=limit,
            page=page,
        )
        if not data.get("lovedtracks"):
            raise CommandError(f"Last.fm user `{username}` not found")

        try:
            return LovedTracks(**data["lovedtracks"])
        except KeyError:
            raise CommandError(
                f"Last.fm user `{username}` doesn't have any loved tracks"
            )

    async def recent_tracks(
        self,
        username: str,
        limit: int = 100,
        page: int = 1,
        start: Optional[int] = None,
        end: Optional[int] = None,
        sk: Optional[str] = None,
    ) -> RecentTracks:
        data = await self.client.get(
            "user.getRecentTracks",
            user=username,
            limit=limit,
            page=page,
            from_=start,
            to=end,
            sk=sk,
        )
        if not data.get("recenttracks"):
            raise CommandError(f"Last.fm user `{username}` not found")

        elif data["status"] == 403:
            raise CommandError(f"Last.fm user `{username}` is not public")

        return RecentTracks(**data["recenttracks"])

    async def top_albums(
        self,
        username: str,
        limit: int = 100,
        page: int = 1,
        period: Period = "overall",
    ) -> TopAlbums:
        data = await self.client.get(
            "user.getTopAlbums",
            user=username,
            period=period,
            limit=limit,
            page=page,
        )
        if not data.get("topalbums"):
            raise CommandError(f"Last.fm user `{username}` not found")

        return TopAlbums(**data["topalbums"])

    async def top_artists(
        self,
        username: str,
        limit: int = 100,
        page: int = 1,
        period: Period = "overall",
    ) -> TopArtists:
        data = await self.client.get(
            "user.getTopArtists",
            user=username,
            period=period,
            limit=limit,
            page=page,
        )
        if not data.get("topartists"):
            raise CommandError(f"Last.fm user `{username}` not found")

        return TopArtists(**data["topartists"])

    async def top_tracks(
        self,
        username: str,
        limit: int = 100,
        page: int = 1,
        period: Period = "overall",
    ) -> TopTracks:
        data = await self.client.get(
            "user.getTopTracks",
            user=username,
            period=period,
            limit=limit,
            page=page,
        )
        if not data.get("toptracks"):
            raise CommandError(f"Last.fm user `{username}` not found")

        return TopTracks(**data["toptracks"])

    async def scrobble(self, track: TrackInfo, sk: str):
        return await self.client.post(
            "track.scrobble",
            sk=sk,
            artist=track.artist,
            track=track.name,
            album=track.album.title if track.album else None,
            mbid=track.mbid,
            timestamp=utcnow().timestamp(),
        )

    async def update_now_playing(self, track: TrackInfo, sk: str):
        return await self.client.post(
            "track.updateNowPlaying",
            sk=sk,
            artist=track.artist,
            track=track.name,
            album=track.album.title if track.album else None,
            mbid=track.mbid,
        )
