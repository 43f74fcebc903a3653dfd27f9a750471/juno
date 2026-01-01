from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from discord.ext.commands import CommandError

from .models import ArtistInfo, ArtistSearch, SimilarArtists, TopAlbums, TopTracks

if TYPE_CHECKING:
    from .. import LastfmClient


class Artist:
    client: LastfmClient

    def __init__(self, client: LastfmClient) -> None:
        self.client = client

    async def info(
        self,
        artist: str,
        username: Optional[str] = None,
    ) -> ArtistInfo:
        data = await self.client.get(
            "artist.getInfo",
            artist=artist,
            extended=1,
            username=username or "",
        )
        if not data.get("artist"):
            raise CommandError(f"Last.fm artist `{artist}` not found")

        return ArtistInfo(**data["artist"])

    async def search(
        self,
        artist: str,
        limit: int = 100,
        page: int = 1,
    ) -> ArtistSearch:
        data = await self.client.get(
            "artist.search",
            artist=artist,
            limit=limit,
            page=page,
        )
        if not data.get("results"):
            raise CommandError(f"Last.fm artist `{artist}` not found")

        try:
            return ArtistSearch(**data["results"]["artistmatches"]["artist"][0])
        except (KeyError, IndexError) as exc:
            raise CommandError(f"Last.fm artist `{artist}` not found") from exc

    async def similar(
        self,
        artist: str,
        limit: int = 100,
        page: int = 1,
    ) -> SimilarArtists:
        data = await self.client.get(
            "artist.getSimilar",
            artist=artist,
            limit=limit,
            page=page,
        )
        if not data.get("similarartists"):
            raise CommandError(f"Last.fm artist `{artist}` not found")

        try:
            return SimilarArtists(**data["similarartists"])
        except (KeyError, IndexError) as exc:
            raise CommandError(f"Last.fm artist `{artist}` not found") from exc

    async def top_albums(
        self,
        artist: str,
        limit: int = 100,
        page: int = 1,
    ) -> TopAlbums:
        data = await self.client.get(
            "artist.getTopAlbums",
            artist=artist,
            limit=limit,
            page=page,
        )
        if not data.get("topalbums"):
            raise CommandError(f"Last.fm artist `{artist}` not found")

        try:
            return TopAlbums(**data["topalbums"])
        except (KeyError, IndexError) as exc:
            raise CommandError(f"Last.fm artist `{artist}` not found") from exc

    async def top_tracks(
        self,
        artist: str,
        limit: int = 100,
        page: int = 1,
    ) -> TopTracks:
        data = await self.client.get(
            "artist.getTopTracks",
            artist=artist,
            limit=limit,
            page=page,
        )
        if not data.get("toptracks"):
            raise CommandError(f"Last.fm artist `{artist}` not found")

        try:
            return TopTracks(**data["toptracks"])
        except (KeyError, IndexError) as exc:
            raise CommandError(f"Last.fm artist `{artist}` not found") from exc
