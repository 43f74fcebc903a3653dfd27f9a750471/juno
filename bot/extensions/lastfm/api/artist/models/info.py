from typing import List, Optional

from pydantic import BaseModel, Field


class ImageItem(BaseModel):
    text: str = Field(..., alias="#text")
    size: str


class Stats(BaseModel):
    listeners: int
    playcount: int
    userplaycount: Optional[int] = 0


class ArtistItem(BaseModel):
    name: str
    url: str
    image: List[ImageItem]


class Similar(BaseModel):
    artist: List[ArtistItem]

    def __str__(self) -> str:
        return ", ".join(artist.name for artist in self.artist)


class TagItem(BaseModel):
    name: str
    url: str


class Tags(BaseModel):
    tag: List[TagItem]

    def __str__(self) -> str:
        return ", ".join(tag.name for tag in self.tag)


class Link(BaseModel):
    text: str = Field(..., alias="#text")
    rel: str
    href: str

    def __str__(self) -> str:
        return self.href


class Links(BaseModel):
    link: Link

    def __str__(self) -> str:
        return self.link.href


class Bio(BaseModel):
    links: Links
    published: str
    summary: str
    content: str

    def __str__(self) -> str:
        return self.summary


class ArtistInfo(BaseModel):
    name: str
    mbid: Optional[str] = None
    url: str
    image: List[ImageItem]
    streamable: str
    ontour: bool
    stats: Stats
    similar: Similar
    tags: Tags
    bio: Bio

    def __str__(self) -> str:
        return self.name

    @property
    def plays(self) -> int:
        return self.stats.userplaycount or 0

    @property
    def image_url(self) -> str:
        return self.image[0].text if self.image else ""
