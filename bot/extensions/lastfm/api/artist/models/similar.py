from typing import List, Optional

from pydantic import BaseModel, Field


class ImageItem(BaseModel):
    text: str = Field(..., alias="#text")
    size: str


class ArtistItem(BaseModel):
    name: str
    mbid: Optional[str] = None
    match: str
    url: str
    image: List[ImageItem]
    streamable: str

    def __str__(self) -> str:
        return self.name


class FieldAttr(BaseModel):
    artist: str


class SimilarArtists(BaseModel):
    artists: List[ArtistItem] = Field(..., alias="artist")
    field_attr: FieldAttr = Field(..., alias="@attr")
