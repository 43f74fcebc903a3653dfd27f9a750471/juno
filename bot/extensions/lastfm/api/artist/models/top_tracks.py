from typing import List, Optional

from pydantic import BaseModel, Field


class Artist(BaseModel):
    name: str
    mbid: Optional[str] = None
    url: str


class ImageItem(BaseModel):
    text: str = Field(..., alias="#text")
    size: str


class FieldAttr(BaseModel):
    rank: int


class TrackItem(BaseModel):
    name: str
    playcount: int
    listeners: int
    mbid: Optional[str] = None
    url: str
    streamable: str
    artist: Artist
    image: List[ImageItem]
    field_attr: FieldAttr = Field(..., alias="@attr")


class FieldAttr1(BaseModel):
    artist: str
    page: int
    perPage: int
    totalPages: int
    total: int


class TopTracks(BaseModel):
    tracks: List[TrackItem] = Field(..., alias="track")
    field_attr: FieldAttr1 = Field(..., alias="@attr")
