from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class MediaItem(BaseModel):
    accessories: list
    height: int
    width: int
    url: str
    type: Optional[str] = "image/png"


class Blog(BaseModel):
    id: str = Field(alias="uuid")
    name: str
    avatar: list[MediaItem]
    has_paywall: bool = Field(False, alias="is_paywall_on")
    posts: int = Field(0)

    def __str__(self) -> str:
        return self.name

    @property
    def url(self) -> str:
        return f"https://{self.name}.tumblr.com/"

    @property
    def avatar_url(self) -> str:
        return self.avatar[0].url


class Post(BaseModel):
    id: str
    url: str = Field(alias="short_url")
    blog: Blog
    content: list
    summary: str
    tags: list
    created_at: datetime = Field(alias="timestamp")
    title: Optional[str] = None
    is_nsfw: bool
    original_blogger: Optional[str] = Field(None, alias="reblogged_root_name")

    @property
    def is_repost(self) -> bool:
        return self.original_blogger is not None


class TumblrResponse(BaseModel):
    meta: dict
    response: dict

    @property
    def posts(self) -> list[Post]:
        return [Post(**post) for post in self.response["posts"]]

    @property
    def total_posts(self) -> int:
        return self.response["totalPosts"]
