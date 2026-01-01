from __future__ import annotations

import re
from datetime import datetime
from random import choice
from typing import Optional

from aiohttp import ClientSession
from pydantic import BaseModel
from yarl import URL

from bot.core import Context
from config import config


class YouTubeUser(BaseModel):
    id: str
    username: str
    description: str
    avatar_url: str
    created_at: datetime

    def __str__(self) -> str:
        return self.username

    @property
    def url(self) -> str:
        return f"https://www.youtube.com/channel/{self.id}"

    @property
    def hyperlink(self) -> str:
        return f"[**{self.username}**]({self.url})"

    @classmethod
    async def fetch(cls, username: str) -> Optional[YouTubeUser]:
        """Fetch a YouTube user by their username."""

        username = username.lstrip("@")
        payload = {"q": username}
        if match := re.match(
            r"https?://(?:www\.)?youtube\.com/channel/(\w+)", username
        ):
            payload.pop("q")
            payload["channelId"] = match.group(1)

        async with ClientSession() as session:
            response = await session.get(
                URL.build(
                    scheme="https",
                    host="www.googleapis.com",
                    path="/youtube/v3/search",
                ),
                params={
                    "part": "snippet",
                    "type": "channel",
                    "key": choice(config.api.youtube),
                    **payload,
                },
            )
            if not response.ok:
                return None

            data = await response.json()
            if not data["items"]:
                return None

            item = data["items"][0]
            return cls(
                id=item["id"]["channelId"],
                username=item["snippet"]["title"],
                description=item["snippet"]["description"],
                avatar_url=item["snippet"]["thumbnails"]["high"]["url"],
                created_at=item["snippet"]["publishedAt"],
            )

    @classmethod
    async def convert(cls, ctx: Context, argument: str) -> YouTubeUser:
        async with ctx.typing():
            user = await cls.fetch(argument)
            if not user:
                raise ValueError(f"No YouTube channel found for `{argument}`")

            return user
