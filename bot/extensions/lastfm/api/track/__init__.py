from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from discord.ext.commands import CommandError

from .models import SimilarTracks, TrackInfo, TrackSearch

if TYPE_CHECKING:
    from .. import LastfmClient


class Track:
    client: LastfmClient

    def __init__(self, client: LastfmClient) -> None:
        self.client = client

    async def info(
        self,
        track: str,
        artist: str,
        username: Optional[str] = None,
        image_url: Optional[str] = None,
    ) -> TrackInfo:
        data = await self.client.get(
            "track.getInfo",
            track=track,
            artist=artist,
            username=username or "",
        )
        if not data.get("track"):
            raise CommandError(f"Last.fm track `{track}` not found")

        return TrackInfo(**data["track"], image_url=image_url)

    async def search(
        self,
        track: str,
        limit: int = 100,
        page: int = 1,
    ) -> TrackSearch:
        data = await self.client.get(
            "track.search",
            track=track,
            limit=limit,
            page=page,
        )
        if not data.get("results"):
            raise CommandError(f"Last.fm track `{track}` not found")

        try:
            return TrackSearch(**data["results"]["trackmatches"]["track"][0])
        except (KeyError, IndexError) as exc:
            raise CommandError(f"Last.fm track `{track}` not found") from exc

    async def similar(
        self,
        track: str,
        artist: str,
        limit: int = 100,
        page: int = 1,
    ) -> SimilarTracks:
        data = await self.client.get(
            "track.getSimilar",
            track=track,
            artist=artist,
            limit=limit,
            page=page,
        )
        if not data.get("similartracks"):
            raise CommandError(f"Last.fm track `{track}` not found")

        try:
            return SimilarTracks(**data["similartracks"])
        except KeyError:
            raise CommandError(f"Last.fm track `{track}` not found")
