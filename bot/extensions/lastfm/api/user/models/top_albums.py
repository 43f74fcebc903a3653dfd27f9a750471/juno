from typing import List, Optional

from pydantic import BaseModel, Field


class Artist(BaseModel):
    url: str
    name: str
    mbid: Optional[str] = None


class ImageItem(BaseModel):
    size: str
    text: str = Field(..., alias="#text")


class FieldAttr(BaseModel):
    rank: int


class AlbumItem(BaseModel):
    artist: Artist
    image: List[ImageItem]
    mbid: Optional[str] = None
    url: str
    playcount: int
    field_attr: FieldAttr = Field(..., alias="@attr")
    name: str

    def __str__(self) -> str:
        return self.name


class FieldAttr1(BaseModel):
    user: str
    totalPages: int
    page: int
    perPage: int
    total: int


class TopAlbums(BaseModel):
    albums: List[AlbumItem] = Field(..., alias="album")
    field_attr: FieldAttr1 = Field(..., alias="@attr")
