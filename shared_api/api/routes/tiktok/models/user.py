import re
from typing import Optional
from datetime import datetime
from api.shared import Media, BaseModel, Field, computed_field
from api.shared.formatter import shorten

class UserStatistics(BaseModel):
    followers: int = Field(validation_alias="followerCount", default=0)
    following: int = Field(validation_alias="followingCount", default=0)
    likes: int = Field(validation_alias="heart", default=0)
    videos: int = Field(validation_alias="videoCount", default=0)

class BioLink(BaseModel):
    url: str = Field(validation_alias="link")

    @computed_field
    @property
    def pretty_url(self) -> str:
        if "discord.gg" in self.url:
            return self.url

        stripped_url = self.url.replace("https://", "").replace("http://", "")
        stripped_url = re.sub(r"[?&].*", "", stripped_url)
        if not self.url.startswith("http"):
            self.url = "https://" + self.url

        return f"[{shorten(stripped_url, 42)}]({self.url})"


class TikTokEvent(BaseModel):
    id: str
    title: str
    starts_at: datetime = Field(validation_alias="start_time")

    @computed_field
    @property
    def url(self) -> str:
        return f"https://www.tiktok.com/live/event/{self.id}"

class TikTokUser(BaseModel):
    id: str
    sec_uid: str = Field(validation_alias="secUid")
    username: str = Field(validation_alias="uniqueId")
    nickname: str
    biography: str = Field(validation_alias="signature")
    avatar: Media = Field(description="Redistributed avatar image.")
    is_verified: bool = Field(validation_alias="verified", default=False)
    is_private: bool = Field(validation_alias="privateAccount", default=False)
    live_id: Optional[str] = Field(validation_alias="roomId", default=None)
    link: Optional[BioLink] = Field(validation_alias="bioLink", default=None)
    events: Optional[list[TikTokEvent]] = Field(validation_alias="eventList", default=None)
    statistics: UserStatistics
    
    @computed_field
    @property
    def url(self) -> str:
        return f"https://www.tiktok.com/@{self.username}"
