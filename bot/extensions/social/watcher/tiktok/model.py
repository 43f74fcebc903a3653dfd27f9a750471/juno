from datetime import datetime
from io import BytesIO
from typing import List, Optional

from aiohttp import ClientSession
from pydantic import BaseModel, Field
from xxhash import xxh128_hexdigest

from config import cookies


class Author(BaseModel):
    id: str
    sec_uid: Optional[str] = Field(alias="secUid")
    username: str = Field(alias="uniqueId")
    full_name: str = Field(alias="nickname")
    avatar_url: str = Field(alias="avatarThumb")
    biography: Optional[str] = Field(alias="signature")

    def __str__(self) -> str:
        return self.full_name or self.username

    @property
    def url(self) -> str:
        return f"https://www.tiktok.com/@{self.username}"


class Statistics(BaseModel):
    like_count: int = Field(alias="diggCount")
    comment_count: int = Field(alias="commentCount")
    play_count: int = Field(alias="playCount")
    share_count: int = Field(alias="shareCount")

    def __str__(self) -> str:
        return f"✨ {self.play_count:,} 💜 {self.like_count:,}"


class Video(BaseModel):
    url: Optional[str] = Field(default=None, alias="playAddr")
    duration: int = Field(alias="duration")
    cover_url: str = Field(default="", alias="cover")

    @property
    def filename(self) -> str:
        if not self.url:
            raise ValueError("No video URL provided")

        return f"{xxh128_hexdigest(self.url)}.mp4"

    async def read(self, id: str) -> BytesIO:
        if not self.url:
            raise ValueError("No video URL provided")

        async with ClientSession(
            headers={
                "Cookie": "; ".join(
                    f"{cookie.name}={cookie.value}" for cookie in cookies
                )
            }
        ) as session:
            url = f"https://tikwm.com/video/media/play/{id}.mp4"
            async with session.get(url) as response:
                buffer = await response.read()

        return BytesIO(buffer)


class Post(BaseModel):
    id: str
    author: Author
    caption: Optional[str] = Field(alias="desc", default="..")
    statistics: Statistics = Field(alias="stats")
    video: Video
    images: List[str] = Field(default=[])
    created_at: datetime = Field(alias="createTime")

    def __str__(self) -> str:
        return self.caption or ".."

    @property
    def url(self) -> str:
        return f"{self.author.url}/video/{self.id}"


class RawAuthor(BaseModel):
    id: str
    unique_id: str
    nickname: str
    avatar_url: str = Field(alias="avatar")


class RawVideo(BaseModel):
    is_top: bool
    id: str = Field(alias="video_id")
    caption: str = Field(alias="title")
    url: str = Field(alias="play")
    duration: int
    create_time: datetime
    author: RawAuthor
    like_count: int = Field(alias="digg_count")
    comment_count: int
    play_count: int
    share_count: int
    ai_dynamic_cover: str
    images: List[str] = Field(default=[])
