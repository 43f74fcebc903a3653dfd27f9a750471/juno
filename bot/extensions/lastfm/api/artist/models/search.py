from typing import List, Optional

from pydantic import BaseModel, Field


class ImageItem(BaseModel):
    text: str = Field(..., alias="#text")
    size: str


class ArtistSearch(BaseModel):
    name: str
    listeners: int
    mbid: Optional[str] = None
    url: str
    streamable: str
    image: List[ImageItem]

    def __str__(self) -> str:
        return self.name
