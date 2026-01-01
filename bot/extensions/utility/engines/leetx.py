from __future__ import annotations

import re
from datetime import datetime
from typing import List, Optional, no_type_check

from aiohttp import ClientSession
from bs4 import BeautifulSoup
from pydantic import BaseModel
from yarl import URL

from . import SAFE_HEADERS


class Torrent(BaseModel):
    url: str
    uploader: str
    title: str
    size: str
    seeders: int
    leechers: int
    uploaded_at: Optional[datetime]

    @classmethod
    @no_type_check
    def parse_html(cls, soup: BeautifulSoup) -> List[Torrent]:
        """Parse the torrents from 1337x HTML."""

        results: List[Torrent] = []
        for row in soup.tbody.find_all("tr"):
            if not row.td:
                continue

            name = row.find("td", class_="coll-1 name").find_all("a")[-1]
            size = row.find("td", class_=re.compile(r"size"))
            if not size:
                continue

            try:
                uploaded = datetime.strptime(
                    re.sub(
                        r"\b(\d+)(st|nd|rd|th)\b",
                        r"\1",
                        row.find("td", class_="coll-date").text,
                    ),
                    "%b. %d '%y",
                )
            except ValueError:
                uploaded = None

            results.append(
                Torrent(
                    url="https://1337x.to" + name["href"],
                    title=name.text,
                    size=size.text.split("B")[0] + "B",
                    seeders=row.find("td", class_="coll-2 seeds").text,
                    leechers=row.find("td", class_="coll-3 leeches").text,
                    uploaded_at=uploaded,
                    uploader=row.find("td", class_=re.compile(r"coll-5")).text,
                )
            )

        return results

    @classmethod
    async def search(cls, query: str) -> List[Torrent]:
        """Search for torrents on 1337x."""

        async with ClientSession() as client:
            response = await client.get(
                URL.build(
                    scheme="https",
                    host="1337x.to",
                    path=f"/search/{query}/1/",
                ),
                headers=SAFE_HEADERS,
            )
            if not response.ok:
                return []

            data = await response.text()
            soup = BeautifulSoup(data, "html.parser")
            return cls.parse_html(soup)
