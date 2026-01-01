from __future__ import annotations

from json import loads
from typing import List

from aiohttp import ClientSession
from bs4 import BeautifulSoup
from pydantic import BaseModel
from yarl import URL


class BingImage(BaseModel):
    url: str
    title: str
    description: str
    image_url: str

    @classmethod
    def replace_unwanted_characters(cls, text: str) -> str:
        """Replace unwanted characters in the text."""

        for char in ("", ""):
            text = text.replace(char, "")

        return text

    @classmethod
    def parse_html(cls, soup: BeautifulSoup) -> List[BingImage]:
        """Parse a Bing image from HTML."""

        images: List[BingImage] = []
        for image in soup.find_all("a", class_="iusc"):
            image = loads(image["m"])
            images.append(
                cls(
                    url=image["purl"],
                    title=cls.replace_unwanted_characters(image.get("t", "")),
                    description=cls.replace_unwanted_characters(image.get("desc", "")),
                    image_url=image["murl"],
                )
            )

        return images

    @classmethod
    async def search(cls, query: str, safe: bool = True) -> List[BingImage]:
        """Search for images on Bing."""

        async with ClientSession() as session:
            response = await session.get(
                URL.build(
                    scheme="https",
                    host="www.bing.com",
                    path="/images/search",
                ),
                params={
                    "q": query,
                    "safeSearch": "Strict" if safe else "Off",
                    "count": 100,
                },
            )
            if not response.ok:
                return []

            html = await response.text()
            soup = BeautifulSoup(html, "html.parser")
            return cls.parse_html(soup)
