from typing import List, Optional

from pydantic import BaseModel, Field


class Artist(BaseModel):
    url: str
    name: str
    mbid: Optional[str] = None


class Date(BaseModel):
    uts: int
    text: str = Field(..., alias="#text")


class ImageItem(BaseModel):
    size: str
    text: str = Field(..., alias="#text")


class Streamable(BaseModel):
    fulltrack: str
    text: str = Field(..., alias="#text")


class TrackItem(BaseModel):
    artist: Artist
    date: Date
    mbid: Optional[str] = None
    url: str
    name: str
    image: List[ImageItem]
    streamable: Streamable

    def __str__(self) -> str:
        return self.name


class FieldAttr(BaseModel):
    user: str
    totalPages: int
    page: int
    perPage: int
    total: int


class LovedTracks(BaseModel):
    tracks: List[TrackItem] = Field(..., alias="track")
    field_attr: FieldAttr = Field(..., alias="@attr")
