from typing import List, Optional

from pydantic import BaseModel, Field


class Artist(BaseModel):
    name: str
    mbid: Optional[str] = None
    url: str


class ImageItem(BaseModel):
    text: str = Field(..., alias="#text")
    size: str


class AlbumItem(BaseModel):
    name: str
    playcount: int
    mbid: Optional[str] = None
    url: str
    artist: Artist
    image: List[ImageItem]


class FieldAttr(BaseModel):
    artist: str
    page: int
    perPage: int
    totalPages: int
    total: int


class TopAlbums(BaseModel):
    albums: List[AlbumItem] = Field(..., alias="album")
    field_attr: FieldAttr = Field(..., alias="@attr")
