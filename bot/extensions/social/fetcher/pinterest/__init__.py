from __future__ import annotations

from json import dumps
from typing import List, Optional
from urllib.parse import quote_plus

from aiohttp import ClientSession
from pydantic import BaseModel, ConfigDict, Field
from yarl import URL

from bot.core import Context


class PinterestBoard(BaseModel):
    model_config = ConfigDict(coerce_numbers_to_str=True)
    id: str
    name: str
    username: str
    pins: int = Field(0, alias="pin_count")

    def __str__(self) -> str:
        return self.name

    @property
    def url(self) -> str:
        slug = self.name.replace("'", "")
        return f"https://www.pinterest.com/{self.username}/{quote_plus(slug)}"

    @property
    def hyperlink(self) -> str:
        return f"[`{self.name}`]({self.url})"


class PinterestUser(BaseModel):
    model_config = ConfigDict(coerce_numbers_to_str=True)
    id: str
    username: str
    display_name: str = Field(..., alias="full_name")
    biography: Optional[str] = Field("", alias="about")
    avatar_url: str = Field(..., alias="image_xlarge_url")
    followers: Optional[int] = Field(0, alias="follower_count")
    following: Optional[int] = Field(0, alias="following_count")
    pins: Optional[int] = Field(0, alias="pin_count")
    private: bool = Field(False, alias="is_private_profile")

    def __str__(self) -> str:
        return self.display_name

    @property
    def url(self) -> str:
        return f"https://www.pinterest.com/{self.username}"

    @property
    def hyperlink(self) -> str:
        return f"[`@{self.username}`]({self.url})"

    async def boards(self) -> List[PinterestBoard]:
        """Fetch a list of Pinterest boards for the user."""

        async with ClientSession() as client:
            response = await client.get(
                URL.build(
                    scheme="https",
                    host="www.pinterest.com",
                    path="/resource/BoardsFeedResource/get/",
                ),
                params={
                    "source_url": f"/{self.username}/_saved/",
                    "data": dumps(
                        {
                            "options": {
                                "username": self.username,
                                "field_set_key": "profile_grid_item",
                                "sort": "last_pinned_to",
                                "filter_stories": False,
                            },
                            "context": {},
                        }
                    ),
                },
            )
            if not response.ok:
                return []

            data = await response.json()
            boards = data["resource_response"]["data"]

            return [
                PinterestBoard(**board, username=self.username)
                for board in boards
                if board.get("name") and board.get("privacy") == "public"
            ]

    @classmethod
    async def fetch(cls, username: str) -> Optional[PinterestUser]:
        """Fetch a Pinterest user by their username."""

        async with ClientSession() as client:
            response = await client.get(
                URL.build(
                    scheme="https",
                    host="www.pinterest.com",
                    path="/resource/UserResource/get/",
                ),
                params={
                    "source_url": f"/{username}/",
                    "data": dumps(
                        {
                            "options": {
                                "username": username,
                                "field_set_key": "unauth_profile",
                                "is_mobile_fork": True,
                            },
                            "context": {},
                        }
                    ),
                },
            )
            if not response.ok:
                return None

            data = await response.json()
            user = data["resource_response"]["data"]

            return cls(**user)

    @classmethod
    async def convert(cls, ctx: Context, argument: str) -> PinterestUser:
        async with ctx.typing():
            user = await cls.fetch(argument)
            if not user:
                raise ValueError(f"No Pinterest user found for `{argument}`")

            return user
