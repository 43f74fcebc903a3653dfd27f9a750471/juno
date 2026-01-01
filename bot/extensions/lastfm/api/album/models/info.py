from typing import List, Optional

from pydantic import BaseModel, Field


class ImageItem(BaseModel):
    size: str
    text: str = Field(..., alias="#text")

    def __str__(self) -> str:
        return self.text


# class FieldAttr(BaseModel):
#     rank: int


class Artist(BaseModel):
    url: str
    name: str
    mbid: Optional[str] = None

    def __str__(self) -> str:
        return self.name


class TrackItem(BaseModel):
    duration: Optional[int] = 0
    url: str
    name: str
    artist: Artist

    def __str__(self) -> str:
        return self.name


class Tracks(BaseModel):
    track: List[TrackItem]

    def __str__(self) -> str:
        return ", ".join(track.name for track in self.track)


class AlbumInfo(BaseModel):
    artist: str
    mbid: Optional[str] = None
    name: str
    userplaycount: int
    image: List[ImageItem]
    tracks: Optional[Tracks] = None
    listeners: int
    playcount: int
    url: str

    def __str__(self) -> str:
        return self.name

    @property
    def plays(self) -> int:
        return self.userplaycount or 0
