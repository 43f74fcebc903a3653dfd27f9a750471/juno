from typing import List, Optional

from pydantic import BaseModel, Field


class ImageItem(BaseModel):
    text: str = Field(..., alias="#text")
    size: str


class AlbumSearch(BaseModel):
    name: str
    artist: str
    url: str
    image: List[ImageItem]
    streamable: str
    mbid: Optional[str] = None

    def __str__(self) -> str:
        return self.name
