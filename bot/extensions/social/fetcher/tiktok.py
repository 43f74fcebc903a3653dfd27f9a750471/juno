from __future__ import annotations

from datetime import datetime
import json
from contextlib import suppress
from json import JSONDecodeError
import re
from typing import Optional

from aiohttp import ClientSession
from bs4 import BeautifulSoup
from pydantic import BaseModel, ConfigDict, Field
from yarl import URL

from bot.core import Context
from bot.shared.formatter import shorten


class TikTokUserStatistics(BaseModel):
    follower_count: int = Field(alias="followerCount", default=0)
    following_count: int = Field(alias="followingCount", default=0)
    heart_count: int = Field(alias="heart", default=0)
    video_count: int = Field(alias="videoCount", default=0)

    def __str__(self) -> str:
        return f"{self.follower_count} followers, {self.following_count} following, {self.heart_count} likes"

    @property
    def variable(self) -> str:
        return "statistics"

    @property
    def fields(self) -> dict[str, str]:
        return {
            "Following": format(self.following_count, ","),
            "Followers": format(self.follower_count, ","),
            "Likes": format(self.heart_count, ","),
        }

class TikTokBioLink(BaseModel):
    url: str = Field(alias="link")

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
    starts_at: datetime = Field(alias="start_time")

    @property
    def url(self) -> str:
        return f"https://www.tiktok.com/live/event/{self.id}"
    
    @property
    def hyperlink(self) -> str:
        return f"[{self.title}]({self.url})"

class TikTokUser(BaseModel):
    model_config = ConfigDict(coerce_numbers_to_str=True)
    id: str
    sec_uid: str = Field(alias="secUid")
    username: str = Field(alias="uniqueId")
    nickname: str
    biography: str = Field(alias="signature")
    avatar_url: str = Field(alias="avatarLarger")
    is_verified: bool = Field(alias="verified", default=False)
    is_private: bool = Field(alias="privateAccount", default=False)
    live_id: Optional[str] = Field(alias="roomId", default=None)
    link: Optional[TikTokBioLink] = Field(alias="bioLink", default=None)
    events: Optional[list[TikTokEvent]] = Field(alias="eventList", default=None)
    statistics: TikTokUserStatistics

    def __str__(self) -> str:
        return self.nickname or self.username

    @property
    def url(self) -> str:
        return f"https://www.tiktok.com/@{self.username}"

    @property
    def display_name(self) -> str:
        username = (
            f"{self.nickname} (@{self.username})"
            if self.nickname and self.nickname != self.username
            else f"@{self.username}"
        )
        if self.is_verified:
            username += " ☑️"
        
        if self.is_private:
            username += " 🔒"

        return username

    @property
    def hyperlink(self) -> str:
        return f"[`@{self.username}`]({self.url})"

    @classmethod
    async def fetch(cls, username: str) -> Optional[TikTokUser]:
        """Fetch a TikTok user by their username."""

        username = username.lstrip("@")
        async with ClientSession() as client:
            response = await client.get(
                URL.build(
                    scheme="https",
                    host="www.tiktok.com",
                    path=f"/@{username}",
                ),
                headers={
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36 Edg/126.0.0.0",
                },
            )
            if not response.ok:
                return None

            html = await response.text()
            soup = BeautifulSoup(html, "html.parser")
            script = soup.find("script", id="__UNIVERSAL_DATA_FOR_REHYDRATION__")
            if not script:
                return None

            with suppress(JSONDecodeError, KeyError):
                data = json.loads(script.text)
                user = data["__DEFAULT_SCOPE__"]["webapp.user-detail"]["userInfo"]
                return cls(**user["user"], statistics=user["stats"])

    @classmethod
    async def convert(cls, ctx: Context, argument: str) -> TikTokUser:
        async with ctx.typing():
            user = await cls.fetch(argument)
            if not user:
                raise ValueError(f"No TikTok user found for `{argument}`")

            return user
