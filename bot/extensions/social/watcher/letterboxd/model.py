from __future__ import annotations

from datetime import datetime
from typing import List

import xmltodict
from pydantic import BaseModel, Field


class LetterboxdFilm(BaseModel):
    id: str
    title: str
    year: int
    member_rating: float
    watched_at: datetime

    def __str__(self) -> str:
        return f"{self.title} ({self.year})"

    @property
    def variable(self) -> str:
        return "film"

    @property
    def url(self) -> str:
        return f"https://letterboxd.com/film/{self.id}/"


class LetterboxdRanking(BaseModel):
    id: str
    url: str = Field(alias="link")
    creator: str = Field(alias="dc:creator")
    description: str
    created_at: datetime
    film: LetterboxdFilm

    def __str__(self) -> str:
        return self.description

    @property
    def creator_url(self) -> str:
        return f"https://letterboxd.com/{self.creator}/"

    @property
    def stars(self) -> str:
        emoji = "`⭐` "
        if self.film.member_rating >= 4.5:
            emoji = "`🌟` "

        return (emoji * max(1, round(self.film.member_rating))).strip()

    @classmethod
    def from_xml(cls, xml: str) -> List[LetterboxdRanking]:
        data = xmltodict.parse(xml)
        rankings = data["rss"]["channel"]
        if "item" not in rankings:
            return []

        rankings = rankings["item"]
        return [
            cls(
                **ranking,
                id=ranking["guid"]["#text"],
                created_at=datetime.strptime(
                    ranking["pubDate"],
                    "%a, %d %b %Y %H:%M:%S %z",
                ),
                film=LetterboxdFilm(
                    id=ranking["tmdb:movieId"],
                    title=ranking["letterboxd:filmTitle"],
                    year=ranking["letterboxd:filmYear"],
                    member_rating=ranking["letterboxd:memberRating"],
                    watched_at=datetime.strptime(
                        ranking["letterboxd:watchedDate"],
                        "%Y-%m-%d",
                    ),
                ),
            )
            for ranking in (rankings if isinstance(rankings, list) else [rankings])
            if ranking.get("letterboxd:memberRating")
        ]
