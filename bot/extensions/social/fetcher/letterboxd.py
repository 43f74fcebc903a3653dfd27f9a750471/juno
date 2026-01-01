from __future__ import annotations

from typing import Optional

from aiohttp import ClientSession
from pydantic import BaseModel, ConfigDict, Field
from yarl import URL

from bot.core import Context


class LetterboxdUser(BaseModel):
    model_config = ConfigDict(coerce_numbers_to_str=True)
    id: str
    username: str
    display_name: str = Field(..., alias="displayName")
    avatar_url: str

    def __str__(self) -> str:
        return self.display_name

    @property
    def url(self) -> str:
        return f"https://letterboxd.com/{self.username}"

    @property
    def hyperlink(self) -> str:
        return f"[`@{self.username}`]({self.url})"

    @classmethod
    async def fetch(cls, username: str) -> Optional[LetterboxdUser]:
        """Fetch a Letterboxd user by their username."""

        async with ClientSession() as client:
            response = await client.get(
                URL.build(
                    scheme="https",
                    host="api.letterboxd.com",
                    path="/api/v0/search",
                ),
                params={"input": username, "include": "MemberSearchItem"},
            )
            if not response.ok:
                return None

            data = await response.json()
            if not data["items"]:
                return None

            user = data["items"][0]["member"]
            return cls(**user, avatar_url=user["avatar"]["sizes"][-1]["url"])

    @classmethod
    async def convert(cls, ctx: Context, argument: str) -> LetterboxdUser:
        async with ctx.typing():
            user = await cls.fetch(argument)
            if not user:
                raise ValueError(f"No Letterboxd user found for `{argument}`")

            return user
