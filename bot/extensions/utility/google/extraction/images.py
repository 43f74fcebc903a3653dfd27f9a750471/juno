from __future__ import annotations

from typing import List

from aiohttp import ClientSession
from discord.ext.commands import CommandError
from pydantic import BaseModel, Field
from yarl import URL

from config import config


class ImageResult(BaseModel):
    url: str
    height: int
    width: int


class GoogleImage(BaseModel):
    color: int
    url: str
    domain: str = Field(alias="footer")
    title: str = Field(alias="header")
    description: str
    image: ImageResult
    thumbnail: ImageResult

    @classmethod
    async def search(cls, query: str, safe: bool = True) -> List[GoogleImage]:
        async with ClientSession() as session:
            async with session.get(
                URL.build(
                    scheme="https",
                    host="notsobot.com",
                    path="/api/search/google/images",
                ),
                params={"query": query[:2000], "safe": str(safe).lower()},
                headers={"Authorization": f"Bot {config.api.notsobot}"},
            ) as response:
                data = await response.json()
                if not response.ok:
                    raise CommandError("Google search failed to return results")

                return [cls(**image) for image in data["results"]]
