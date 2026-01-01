from __future__ import annotations

import re
from json import JSONDecodeError, loads
from typing import Optional

from aiohttp import ClientSession
from discord import Color
from pydantic import BaseModel, Field

from bot.core import Context


class Avatar(BaseModel):
    url: Optional[str] = Field(None, alias="image_url")
    accent_color: str

    @property
    def color(self) -> Color:
        return Color(int(self.accent_color.strip("#"), 16))


class CashAppUser(BaseModel):
    username: str
    display_name: str
    country_code: Optional[str] = "Unknown"
    avatar: Avatar

    @property
    def url(self) -> str:
        return f"https://cash.app/${self.username}"

    @property
    def qr_code(self) -> str:
        return f"https://cash.app/qr/${self.username}?size=288&margin=0"

    @classmethod
    async def fetch(cls, username: str) -> Optional[CashAppUser]:
        """Fetch a CashApp user by their username."""

        username = username.lstrip("$")
        async with ClientSession() as session:
            async with session.get(f"https://cash.app/{username}") as response:
                if not response.ok:
                    return None

                data = await response.text()
                match = re.search(r"var profile = ({.*?});", data, re.DOTALL)
                if not match:
                    return None

                try:
                    profile = loads(match[1])
                except JSONDecodeError:
                    return None

                return cls(
                    username=username,
                    display_name=profile["display_name"],
                    country_code=profile["country_code"],
                    avatar=Avatar(**profile["avatar"]),
                )

    @classmethod
    async def convert(cls, ctx: Context, argument: str) -> CashAppUser:
        async with ctx.typing():
            user = await cls.fetch(argument)
            if not user:
                raise ValueError(f"No CashApp user found for `{argument}`")

            return user
