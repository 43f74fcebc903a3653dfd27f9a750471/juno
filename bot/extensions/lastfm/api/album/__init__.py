from __future__ import annotations

from typing import TYPE_CHECKING, Optional, cast

from discord.ext.commands import CommandError

from .models import AlbumInfo, AlbumSearch

if TYPE_CHECKING:
    from .. import LastfmClient


class Album:
    client: LastfmClient

    def __init__(self, client: LastfmClient) -> None:
        self.client = client

    async def info(
        self,
        album: str,
        artist: str,
        username: Optional[str] = None,
    ) -> AlbumInfo:
        data = await self.client.get(
            "album.getInfo",
            album=album,
            artist=artist,
            username=username or "",
        )
        if not data.get("album"):
            raise CommandError(f"Last.fm album `{album}` not found")

        data = cast(dict, data["album"])
        if isinstance(data.get("tracks", {}).get("track", None), dict):
            data["tracks"]["track"] = [data["tracks"]["track"]]

        return AlbumInfo(**data)

    async def search(
        self,
        album: str,
        limit: int = 100,
        page: int = 1,
    ) -> AlbumSearch:
        data = await self.client.get(
            "album.search",
            album=album,
            limit=limit,
            page=page,
        )
        if not data.get("results"):
            raise CommandError(f"Last.fm album `{album}` not found")

        try:
            return AlbumSearch(**data["results"]["albummatches"]["album"][0])
        except (KeyError, IndexError) as exc:
            raise CommandError(f"Last.fm album `{album}` not found") from exc
