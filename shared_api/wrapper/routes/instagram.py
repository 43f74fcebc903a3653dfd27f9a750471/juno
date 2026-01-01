from __future__ import annotations
from typing import TYPE_CHECKING
from .model import InstagramUser, InstagramStoryResponse, Highlight

if TYPE_CHECKING:
    from .. import SharedAPI

class Instagram:
    api: SharedAPI

    def __init__(self, api: SharedAPI) -> None:
        self.api = api

    async def user(self, username: str) -> InstagramUser:
        """Fetch an Instagram user's profile."""

        data = await self.api.request("GET", f"/instagram/{username}")
        return InstagramUser(**data)
    
    async def story(self, username: str) -> InstagramStoryResponse:
        """Fetch an Instagram user's story."""

        data = await self.api.request("GET", f"/instagram/{username}/story")
        return InstagramStoryResponse(**data)
    
    async def highlight(self, highlight_id: str) -> Highlight:
        """Fetch an Instagram highlight."""

        data = await self.api.request("GET", f"/instagram/highlight/{highlight_id}")
        return Highlight(**data)