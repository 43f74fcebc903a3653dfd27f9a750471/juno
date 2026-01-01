from typing import List, Optional

from pydantic import BaseModel, Field


class Streamable(BaseModel):
    fulltrack: str
    text: str = Field(..., alias="#text")


class ImageItem(BaseModel):
    size: str
    text: str = Field(..., alias="#text")


class Artist(BaseModel):
    url: str
    name: str
    mbid: Optional[str] = None


class FieldAttr(BaseModel):
    rank: int


class TrackItem(BaseModel):
    streamable: Streamable
    mbid: Optional[str] = None
    name: str
    image: List[ImageItem]
    artist: Artist
    url: str
    duration: int
    field_attr: FieldAttr = Field(..., alias="@attr")
    playcount: int

    def __str__(self) -> str:
        return self.name


class FieldAttr1(BaseModel):
    user: str
    totalPages: int
    page: int
    perPage: int
    total: int


class TopTracks(BaseModel):
    tracks: List[TrackItem] = Field(..., alias="track")
    field_attr: FieldAttr1 = Field(..., alias="@attr")
