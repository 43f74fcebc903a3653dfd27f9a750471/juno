from __future__ import annotations

from typing import List, Optional, no_type_check

from aiohttp import ClientSession
from bs4 import BeautifulSoup
from markdownify import markdownify as get_text
from pydantic import BaseModel, ConfigDict
from yarl import URL

BASE_URL = URL.build(
    scheme="https",
    host="dumb.ducks.party",
)


class Artist(BaseModel):
    url: str
    name: str


class Genius(BaseModel):
    model_config = ConfigDict(coerce_numbers_to_str=True)
    url: str
    title: str
    artist: Artist
    thumbnail_url: str
    producers: Optional[List[str]]
    lyrics: str

    @classmethod
    @no_type_check
    async def search(cls, query: str) -> Optional[Genius]:
        """Search for lyrics with Genius."""

        async with ClientSession() as session:
            response = await session.get(
                BASE_URL.with_path("/search"),
                params={"q": query},
            )
            if not response.ok:
                return None

            html = await response.text()
            soup = BeautifulSoup(html, "html.parser")
            results = soup.find_all("a", id="search-item")
            if not results:
                return None

            result = results[0]
            response = await session.get(BASE_URL.with_path(result["href"]))
            if not response.ok:
                return None

            html = await response.text()
            soup = BeautifulSoup(html, "html.parser")
            lyrics = get_text(
                str(soup.find("div", id="lyrics")),
                strip=["a"],
            )
            metadata = soup.find("div", id="metadata")
            if not lyrics or not metadata:
                return None

            producers: Optional[List[str]] = None
            for details in soup.find("div", id="credits").find_all("details"):
                if details.find("summary").text == "Producers":
                    producers = get_text(str(details.p)).strip().split(", ")
                    break

            return cls(
                url="https://genius.com" + result["href"],
                title=metadata.h1.text,
                artist=Artist(
                    url="https://genius.com" + metadata.a["href"],
                    name=metadata.a.text,
                ),
                thumbnail_url="https://images.genius.com/"
                + metadata.img["src"].replace("/images/", ""),
                lyrics=lyrics,
                producers=producers,
            )
