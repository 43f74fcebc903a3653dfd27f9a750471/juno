import re
from api.shared import Media, BaseModel, Field, computed_field
from api.shared.formatter import shorten
from typing import Optional, List, Any


class UserStatistics(BaseModel):
    posts: int = Field(default=0)
    followers: int = Field(default=0)
    following: int = Field(default=0)


class BioLink(BaseModel):
    url: str
    link_id: Optional[str] = Field(default=None)
    lynx_url: Optional[str] = Field(default=None)
    title: Optional[str] = Field(default=None)

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


class SimpleUser(BaseModel):
    id: str = Field(validation_alias="pk")
    username: str
    full_name: Optional[str] = Field(default=None)
    avatar: Media = Field(description="Redistributed avatar image.")
    is_verified: bool = Field(default=False)
    is_private: bool = Field(default=False)


class InstagramUser(SimpleUser):
    id: str = Field(validation_alias="pk")
    username: str
    full_name: Optional[str] = Field(default=None)
    biography: Optional[str] = Field(default=None)
    avatar: Media = Field(description="Redistributed avatar image.")
    is_verified: bool = Field(default=False)
    is_private: bool = Field(default=False)
    statistics: UserStatistics = Field()
    links: List[BioLink] = Field(default=[], validation_alias="bio_links")
    highlights: Optional[List[Any]] = Field(default=[])

    @computed_field
    @property
    def url(self) -> str:
        return f"https://www.instagram.com/{self.username}"
