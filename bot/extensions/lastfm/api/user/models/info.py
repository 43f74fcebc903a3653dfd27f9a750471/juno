from datetime import UTC, datetime, timedelta
from typing import List

from discord.utils import utcnow
from pydantic import BaseModel, Field


class ImageItem(BaseModel):
    size: str
    text: str = Field(..., alias="#text")


class Registered(BaseModel):
    unixtime: int
    text: int = Field(..., alias="#text")

    @property
    def date(self) -> datetime:
        return datetime.fromtimestamp(self.unixtime, tz=UTC)


class UserInfo(BaseModel):
    name: str
    age: int
    subscriber: bool
    realname: str
    bootstrap: bool
    playcount: int
    artist_count: int
    playlists: int
    track_count: int
    album_count: int
    image: List[ImageItem]
    registered: Registered
    country: str
    gender: str
    url: str
    type: str

    def __str__(self) -> str:
        return self.name

    @property
    def scrobbles(self) -> int:
        return self.playcount

    @property
    def avatar_url(self) -> str:
        return self.image[-1].text.replace(".png", ".gif")

    @property
    def average(self) -> float:
        return self.playcount / (utcnow() - self.registered.date).days

    def milestone_date(self, milestone: int) -> datetime:
        return self.registered.date + timedelta(days=milestone / self.average)
