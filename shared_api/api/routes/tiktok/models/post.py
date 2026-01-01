from datetime import datetime
from typing import List, Optional
from api.shared import Media, BaseModel, Field, computed_field

class Author(BaseModel):
    id: str
    sec_uid: Optional[str] = Field(validation_alias="secUid")
    username: str = Field(validation_alias="uniqueId")
    full_name: str = Field(validation_alias="nickname")
    avatar: Media = Field(description="Redistributed avatar image.")
    biography: Optional[str] = Field(default=None, validation_alias="signature")

    @computed_field
    @property
    def url(self) -> str:
        return f"https://www.tiktok.com/@{self.username}"


class Statistics(BaseModel):
    likes: int = Field(validation_alias="diggCount")
    comments: int = Field(validation_alias="commentCount")
    views: int = Field(validation_alias="playCount")
    shares: int = Field(validation_alias="shareCount")

class TikTokPost(BaseModel):
    id: str
    author: Author
    caption: Optional[str] = Field(validation_alias="desc", default="..")
    statistics: Statistics = Field(validation_alias="stats")
    video: Optional[Media] = Field(default=None)
    images: List[Media] = Field(default=[])
    created_at: Optional[datetime] = Field(default=None, validation_alias="createTime")

    @computed_field
    @property
    def url(self) -> str:
        return f"{self.author.url}/video/{self.id}"