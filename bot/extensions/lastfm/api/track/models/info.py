from typing import List, Optional

from pydantic import BaseModel, Field


class Artist(BaseModel):
    name: str
    mbid: Optional[str] = None
    url: str

    def __str__(self) -> str:
        return self.name


class ImageItem(BaseModel):
    text: str = Field(..., alias="#text")
    size: str

    def __str__(self) -> str:
        return self.text


class Album(BaseModel):
    artist: str
    title: str
    mbid: Optional[str] = None
    url: str
    image: List[ImageItem]

    def __str__(self) -> str:
        return self.title


class Wiki(BaseModel):
    published: str
    summary: str
    content: str

    def __str__(self) -> str:
        return self.summary


class TrackInfo(BaseModel):
    name: str
    mbid: Optional[str] = None
    url: str
    duration: int
    listeners: int
    playcount: int
    artist: Artist
    album: Optional[Album] = None
    userplaycount: Optional[int] = 0
    userloved: Optional[bool] = False
    wiki: Optional[Wiki] = None
    image_url: Optional[str] = None

    def __str__(self) -> str:
        return self.name

    @property
    def plays(self) -> int:
        return self.userplaycount or 0
