from __future__ import annotations
import asyncio
from contextlib import suppress
import os
from typing import TYPE_CHECKING, Optional, cast
from xxhash import xxh32_hexdigest
from bot.core import Juno
from anyio import Path

from .. import Reposter
from .extraction import Information
from .extraction.model import RequestedDownload
from aiograpi import Client as InstagramClient
from aiograpi.types import Media as Post, Highlight
from aiograpi.exceptions import MediaNotFound, InvalidMediaId

if TYPE_CHECKING:
    from .. import Social, InstagramWatcher


class Instagram(Reposter):
    def __init__(self, bot: Juno) -> None:
        super().__init__(
            bot,
            regex=[
                r"\<?(https?://(?:www\.)?instagram\.com(?:/[^/]+)?/(?:p|tv|reel|reels)/(?P<post_id>[^/?#&]+))\>?",
                r"\<?(https?://(?:www\.)?instagram\.com/stories/highlights/(?P<highlight_id>[^/?#&]+))\>?",
                r"\<?(https?://(?:www\.)?instagram\.com/stories/(?P<username>[^/?#&]+)/(?P<story_id>[^/?#&]+))\>?",
            ],
        )

    @property
    def client(self) -> InstagramClient:
        social = cast(Optional["Social"], self.bot.get_cog("Social"))
        if not social:
            raise ValueError("Social cog is not loaded")

        instagram: Optional["InstagramWatcher"] = None
        for watcher in social.watchers:
            if watcher.name == "Instagram":
                instagram = watcher  # type: ignore
                break

        if not instagram:
            raise ValueError("The Instagram watcher is not loaded")

        return instagram.client

    async def fetch(self, url: str) -> Information | None:
        match = self.match(url)
        if not match:
            return None

        group = match.groupdict()
        try:
            if highlight_id := group.get("highlight_id"):
                data = await self.client.highlight_info(highlight_id)
                origin = f"https://www.instagram.com/stories/highlights/{highlight_id}"
            elif story_id := group.get("story_id"):
                data = await self.client.story_info(story_id)
                origin = f"https://www.instagram.com/stories/{group['username']}/{story_id}"
            else:
                media_pk = await self.client.media_pk_from_code(group["post_id"])
                data = await self.client.media_info(media_pk)
                origin = f"https://www.instagram.com/p/{group['post_id']}"
        except (Exception, MediaNotFound, InvalidMediaId):
            return None

        folder = Path("/tmp/juno") / xxh32_hexdigest(data.id)
        files = []
        if not await folder.exists():
            await folder.mkdir(parents=True, exist_ok=True)
            resources = []
            if isinstance(data, Post):
                if data.media_type == 2:
                    resources = [data]
                else:
                    resources = data.resources

            elif isinstance(data, Highlight):
                resources = data.items[:30]
            else:
                resources = [data]
            
            files = await asyncio.gather(
                *[
                    self.client.media_download(resource_url, origin, folder=folder)
                    for resource in resources
                    if (resource_url := resource.video_url or resource.thumbnail_url)
                ]
            )

            if not files and isinstance(data, Post):
                if data.thumbnail_url:
                    with suppress(Exception):
                        file = await self.client.media_download(data.thumbnail_url, origin, folder=folder)
                        files.append(file)

        return Information(
            id=data.pk,
            title=f"Instagram {data.__class__.__name__.lower()}",
            requested_downloads=[
                RequestedDownload(epoch=0, filepath=f"{folder}/{file}")
                for file in os.listdir(folder)
            ],
        )  # type: ignore
