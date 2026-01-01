from typing import List, Optional

from pydantic import BaseModel, Field


class Streamable(BaseModel):
    text: str = Field(..., alias="#text")
    fulltrack: str


class Artist(BaseModel):
    name: str
    mbid: Optional[str] = None
    url: str


class ImageItem(BaseModel):
    text: str = Field(..., alias="#text")
    size: str


class TrackItem(BaseModel):
    name: str
    playcount: int
    mbid: Optional[str] = None
    match: float
    url: str
    streamable: Streamable
    duration: Optional[int] = None
    artist: Artist
    image: List[ImageItem]

    def __str__(self) -> str:
        return self.name


class FieldAttr(BaseModel):
    artist: str


class SimilarTracks(BaseModel):
    tracks: List[TrackItem] = Field(..., alias="track")
    field_attr: FieldAttr = Field(..., alias="@attr")
