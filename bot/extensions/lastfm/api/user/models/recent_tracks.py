from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from asyncspotify import SimpleTrack as SpotifyTrack
from pydantic import BaseModel, ConfigDict, Field

from ...track.models.info import TrackInfo


class Artist(BaseModel):
    mbid: Optional[str] = None
    text: str = Field(..., alias="#text")

    def __str__(self) -> str:
        return self.text

    @property
    def name(self) -> str:
        return self.text

    @property
    def url(self) -> str:
        return f"https://www.last.fm/music/{self.text.replace(' ', '+')}"


class ImageItem(BaseModel):
    size: str
    text: str = Field(..., alias="#text")

    def __str__(self) -> str:
        return self.text


class Album(BaseModel):
    mbid: Optional[str] = None
    text: str = Field(..., alias="#text")

    def __str__(self) -> str:
        return self.text

    def __bool__(self) -> bool:
        return bool(self.text)

    @property
    def title(self) -> str:
        return self.text


class FieldAttr(BaseModel):
    nowplaying: bool


class Date(BaseModel):
    uts: int
    text: str = Field(..., alias="#text")

    @property
    def timestamp(self) -> datetime:
        return datetime.fromtimestamp(self.uts)


class TrackItem(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)
    artist: Artist
    streamable: str
    image: List[ImageItem]
    mbid: Optional[str] = None
    album: Album
    name: str
    field_attr: Optional[FieldAttr] = Field(None, alias="@attr")
    url: str
    date: Optional[Date] = None
    data: Optional[TrackInfo | TrackItem] = None
    spotify: Optional[SpotifyTrack] = None

    def __str__(self) -> str:
        return self.name

    @property
    def image_url(self) -> str:
        return self.image[-1].text


class FieldAttr1(BaseModel):
    user: str
    totalPages: int
    page: int
    perPage: int
    total: int


class RecentTracks(BaseModel):
    tracks: List[TrackItem] = Field(..., alias="track")
    field_attr: FieldAttr1 = Field(..., alias="@attr")
