from typing import List, Optional

from pydantic import BaseModel, Field


class ImageItem(BaseModel):
    text: str = Field(..., alias="#text")
    size: str


class TrackSearch(BaseModel):
    name: str
    artist: str
    url: str
    streamable: str
    listeners: int
    image: List[ImageItem]
    mbid: Optional[str] = None

    def __str__(self) -> str:
        return self.name
