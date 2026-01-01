from __future__ import annotations

from datetime import datetime
from json import JSONDecodeError, loads
from socket import AF_INET
from typing import List, Optional

from aiohttp import ClientSession, TCPConnector
from aiohttp_proxy import ProxyConnector
from discord.utils import utcnow
from pydantic import BaseModel, ConfigDict, Field
from yarl import URL

from bot.core import Context
from config import config


class KickURL(BaseModel):
    url: str
    responsive: Optional[str] = Field(None)

    def __str__(self) -> str:
        return self.dynamic_url

    @property
    def dynamic_url(self) -> str:
        return self.url + f"?t={utcnow().timestamp()}"

    @property
    def variable(self) -> str:
        return "url"


class KickURLSource(BaseModel):
    src: str
    srcset: str

    def __str__(self) -> str:
        return self.src

    @property
    def variable(self) -> str:
        return "url"


class Category(BaseModel):
    model_config = ConfigDict(coerce_numbers_to_str=True)
    id: str = Field(alias="category_id")
    name: str
    slug: str
    viewers: int


class KickLivestream(BaseModel):
    model_config = ConfigDict(coerce_numbers_to_str=True)
    id: str
    slug: str
    channel_id: str
    created_at: datetime
    started_at: datetime = Field(alias="start_time")
    title: str = Field(alias="session_title")
    is_live: bool
    viewer_count: int
    thumbnail: KickURL
    categories: List[Category]

    def __str__(self) -> str:
        return self.title

    def __bool__(self) -> bool:
        return self.is_live

    @property
    def variable(self) -> str:
        return "stream"


class KickUser(BaseModel):
    model_config = ConfigDict(coerce_numbers_to_str=True)
    id: str
    username: str
    display_name: str
    biography: Optional[str]
    created_at: datetime
    is_verified: bool
    is_banned: bool
    instagram: str
    twitter: str
    youtube: str
    discord: str
    tiktok: str
    avatar_url: Optional[str] = None
    banner_image: Optional[KickURL] = None
    offline_banner_image: Optional[KickURLSource] = None
    recent_categories: List[Category]
    stream: Optional[KickLivestream] = None

    def __str__(self) -> str:
        return self.display_name or self.username

    @property
    def variable(self) -> str:
        return "user"

    @property
    def url(self) -> str:
        return f"https://kick.com/{self.username}"

    @property
    def hyperlink(self) -> str:
        return f"[`@{self.username}`]({self.url})"

    @classmethod
    async def fetch(cls, username: str) -> Optional[KickUser]:
        """Fetch a Kick user by their username."""

        username = username.lstrip("@")
        async with ClientSession(
            connector=(
                ProxyConnector.from_url(config.http_proxy)
                if config.http_proxy
                else TCPConnector(family=AF_INET)
            )
        ) as client:
            # response = await client.get(
            #     URL.build(
            #         scheme="https",
            #         host="kick.com",
            #         path=f"/api/v2/channels/{username}",
            #     ),
            # )
            # if not response.ok:
            #     return None

            # data = await response.json(content_type="text/html")
            response = await client.post(
                URL.build(
                    scheme="http",
                    host="0.0.0.0:8191",
                    path="/v1",
                ),
                json={
                    "cmd": "request.get",
                    "url": f"https://kick.com/api/v2/channels/{username}",
                },
            )
            if not response.ok:
                return None

            data = await response.json()
            body = data["solution"]["response"]
            try:
                data = loads(body[body.index("{") : body.rindex("}") + 1])
            except JSONDecodeError:
                return None

            return cls(
                id=data["user_id"],
                username=data["slug"],
                display_name=data["user"]["username"],
                biography=data["user"]["bio"],
                created_at=data["user"]["email_verified_at"],
                is_verified=data["verified"],
                is_banned=data["is_banned"],
                instagram=data["user"]["instagram"],
                twitter=data["user"]["twitter"],
                youtube=data["user"]["youtube"],
                discord=data["user"]["discord"],
                tiktok=data["user"]["tiktok"],
                avatar_url=data["user"]["profile_pic"],
                banner_image=(
                    KickURL(**data["banner_image"])
                    if data.get("banner_image")
                    else None
                ),
                offline_banner_image=(
                    KickURLSource(**data["offline_banner_image"])
                    if data["offline_banner_image"]
                    else None
                ),
                recent_categories=[
                    Category(**category) for category in data["recent_categories"]
                ],
                stream=(
                    KickLivestream(**data["livestream"]) if data["livestream"] else None
                ),
            )

    @classmethod
    async def convert(cls, ctx: Context, argument: str) -> KickUser:
        async with ctx.typing():
            user = await cls.fetch(argument)
            if not user:
                raise ValueError(f"No Kick user found for `{argument}`")

            return user
