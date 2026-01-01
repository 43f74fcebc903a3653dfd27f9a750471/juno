from __future__ import annotations

from datetime import datetime
from typing import Optional, no_type_check

from aiohttp import ClientSession
from bs4 import BeautifulSoup
from pydantic import BaseModel
from yarl import URL

from . import SAFE_HEADERS, get_text


class Author(BaseModel):
    url: str
    name: str
    avatar_url: str


class Book(BaseModel):
    url: str
    author: Author
    title: str
    description: str
    ratings: int
    reviews: int
    rating: float
    published: datetime
    pages: str
    cover_url: str

    @property
    def stars(self) -> str:
        emoji = "`⭐` "
        if self.rating >= 4.5:
            emoji = "`🌟` "

        return (emoji * max(1, round(self.rating))).strip()

    @classmethod
    @no_type_check
    def parse_html(cls, soup: BeautifulSoup) -> Book:
        """Parse a book from Goodreads HTML."""

        contributor = soup.find("div", class_="FeaturedPerson")
        author = Author(
            name=get_text(contributor.find("span", class_="ContributorLink__name")),
            url=contributor.find("a", class_="ContributorLink")["href"],
            avatar_url=contributor.find("img", class_="Avatar__image")["src"],
        )

        return cls(
            author=author,
            url=soup.find("link", rel="canonical")["href"],
            title=soup.find("h1", {"data-testid": "bookTitle"}).text,
            description=soup.find("div", {"data-testid": "contentContainer"}).text,
            ratings=int(
                soup.find("span", {"data-testid": "ratingsCount"})
                .text.split("\xa0", 1)[0]
                .replace(",", "")
                .strip()
            ),
            reviews=int(
                soup.find("span", {"data-testid": "reviewsCount"})
                .text.split("\xa0", 1)[0]
                .replace(",", "")
                .strip()
            ),
            rating=float(soup.find("div", class_="RatingStatistics__rating").text),
            published=datetime.strptime(
                soup.find("p", {"data-testid": "publicationInfo"}).text,
                "First published %B %d, %Y",
            ),
            pages=soup.find("p", {"data-testid": "pagesFormat"}).text,
            cover_url=soup.find("div", class_="BookCover").img["src"],
        )

    @classmethod
    async def search(cls, query: str) -> Optional[Book]:
        """Search for books on Goodreads."""

        async with ClientSession(headers=SAFE_HEADERS) as session:
            response = await session.get(
                URL.build(
                    scheme="https",
                    host="www.goodreads.com",
                    path="/search",
                ),
                params={"q": query},
            )
            if not response.ok:
                return None

            html = await response.text()
            soup = BeautifulSoup(html, "html.parser")
            result = soup.find("tr", itemtype="http://schema.org/Book")
            if not result:
                return None

            response = await session.get(
                URL.build(
                    scheme="https",
                    host="www.goodreads.com",
                    path=result.a["href"],  # type: ignore
                ),
            )
            if not response.ok:
                return None

            html = await response.text()
            soup = BeautifulSoup(html, "html.parser")
            return cls.parse_html(soup)
