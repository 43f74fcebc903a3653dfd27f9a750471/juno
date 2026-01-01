from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from aiohttp import ClientSession
from pydantic import BaseModel, ConfigDict, Field
from yarl import URL

from bot.core import Context


class SoundCloudTrack(BaseModel):
    model_config = ConfigDict(coerce_numbers_to_str=True)
    id: str
    kind: str
    title: str
    plays: Optional[int] = Field(..., alias="playback_count")
    created_at: datetime
    image_url: Optional[str] = Field(..., alias="artwork_url")
    url: str = Field(..., alias="permalink_url")
    user: SoundCloudUser

    def __str__(self) -> str:
        return self.title

    @property
    def variable(self) -> str:
        return "track"


class SoundCloudUser(BaseModel):
    model_config = ConfigDict(coerce_numbers_to_str=True)
    id: str
    username: str = Field(..., alias="permalink")
    display_name: str = Field(..., alias="username")
    created_at: Optional[datetime] = None
    track_count: Optional[int] = 0
    followers_count: Optional[int] = 0
    following_count: Optional[int] = Field(0, alias="followings_count")
    avatar_url: str

    def __str__(self) -> str:
        return self.display_name

    @property
    def variable(self) -> str:
        return "user"

    @property
    def url(self) -> str:
        return f"https://soundcloud.com/{self.username}"

    @property
    def hyperlink(self) -> str:
        return f"[`@{self.username}`]({self.url})"

    @classmethod
    async def fetch(cls, username: str) -> Optional[SoundCloudUser]:
        """Fetch a SoundCloud user by their username."""

        username = username.lstrip("@")
        async with ClientSession() as client:
            response = await client.get(
                URL.build(
                    scheme="https",
                    host="api-v2.soundcloud.com",
                    path="/search/users",
                ),
                params={"q": username, "limit": 20},
                headers={"Authorization": "OAuth 2-292593-994587358-Af8VbLnc6zIplJ"},
            )
            if not response.ok:
                return None

            data = await response.json()
            if not data["collection"]:
                return None

            user = next(
                (
                    item
                    for item in data["collection"]
                    if item["permalink"].lower() == username.lower()
                ),
                data["collection"][0],
            )
            return cls(**user)

    @classmethod
    async def tracks(cls, user_id: str) -> List[SoundCloudTrack]:
        """Fetch a SoundCloud user's tracks by their ID."""

        async with ClientSession() as client:
            response = await client.get(
                URL.build(
                    scheme="https",
                    host="api-v2.soundcloud.com",
                    path=f"/users/{user_id}/tracks",
                ),
                headers={"Authorization": "OAuth 2-292593-994587358-Af8VbLnc6zIplJ"},
            )
            if not response.ok:
                return []

            data = await response.json()
            return [SoundCloudTrack(**item) for item in data["collection"]]

    @classmethod
    async def convert(cls, ctx: Context, argument: str) -> SoundCloudUser:
        async with ctx.typing():
            user = await cls.fetch(argument)
            if not user:
                raise ValueError(f"No SoundCloud user found for `{argument}`")

            return user


SoundCloudTrack.model_rebuild()
