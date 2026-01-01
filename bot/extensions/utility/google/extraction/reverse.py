from __future__ import annotations

import re
from logging import getLogger
from typing import List, Optional

from bs4 import BeautifulSoup, NavigableString, Tag
from cashews import cache
from pydantic import BaseModel, Field
from yarl import URL

from bot.core import Juno
from bot.shared.formatter import plural, shorten
from .search import SAFE_HEADERS

logger = getLogger("bot.google")


def get_text(tag: Optional[Tag | NavigableString]) -> Optional[str]:
    if not tag:
        return None

    return tag.text


class Result(BaseModel):
    url: str
    title: str
    description: str

    @property
    def pretty_url(self) -> str:
        stripped_url = self.url.split("/", 2)[-1]
        stripped_url = re.sub(r"[?&].*", "", stripped_url)

        return f"[**{shorten(stripped_url, 42)}**]({self.url})"

    @classmethod
    def from_soup(cls, soup: BeautifulSoup) -> List[Result]:
        results: List[Result] = []

        parents = soup.find_all("div", class_="MjjYud")
        if not parents:
            return results
        
        for child in parents[-1].find_all("div", jscontroller="SC7lYd"):
            try:
                url = child.a["href"]
                title = child.h3.text
                description = (
                    child.find("div", class_=re.compile("VwiC3b yXK7lf"))
                    .find_all("span")[-1]
                    .text
                )
            except (IndexError, AttributeError):
                continue

            results.append(Result(url=url, title=title, description=description))

        logger.info(
            f"Extracted {plural(len(results)):result} from Google Reverse Image Search"
        )
        return results


class GoogleReverse(BaseModel):
    related: Optional[str]
    results: List[Result]
    statistics: Optional[str] = Field("..")

    @property
    def related_url(self) -> Optional[URL]:
        if not self.related:
            return None

        return URL(f"https://www.google.com/search?q={self.related}")
    
    @classmethod
    @cache(ttl="1m", key="{image_url}:{safe}")
    async def search(
        cls,
        bot: Juno,
        image_url: str,
        safe: bool = True,
    ) -> GoogleReverse:
        async with bot.session.get(
            URL.build(
                scheme="https",
                host="www.google.com",
                path="/searchbyimage",
                query={
                    "image_url": image_url,
                    "safe": safe * "active" or "off",
                    "sbisrc": "tg",
                },
            ),
            headers=SAFE_HEADERS,
        ) as response:
            html = await response.text()
            soup = BeautifulSoup(html, "html.parser")

            return cls(
                related=get_text(soup.find("a", class_="fKDtNb")),
                results=Result.from_soup(soup),
                statistics=get_text(soup.find("div", id="result-stats")),
            )
