from __future__ import annotations
from typing import TYPE_CHECKING
from .model import TikTokUser, TikTokPostsResponse, TikTokPost

if TYPE_CHECKING:
    from .. import SharedAPI


class TikTok:
    api: SharedAPI

    def __init__(self, api: SharedAPI) -> None:
        self.api = api

    async def user(self, username: str) -> TikTokUser:
        """Fetch a a TikTok user's profile."""

        data = await self.api.request("GET", f"/tiktok/{username}")
        return TikTokUser(**data)

    async def posts(self, username: str) -> TikTokPostsResponse:
        """Fetch a TikTok user's posts."""

        data = await self.api.request("GET", f"/tiktok/{username}/posts")
        return TikTokPostsResponse(**data)

    async def reposts(self, username: str) -> TikTokPostsResponse:
        """Fetch a TikTok user's reposts."""

        data = await self.api.request("GET", f"/tiktok/{username}/reposts")
        return TikTokPostsResponse(**data)

    async def post(self, url: str) -> TikTokPost:
        """Fetch a TikTok video or slideshow."""

        data = await self.api.request("GET", "/tiktok/post", params={"url": url})
        return TikTokPost(**data)
