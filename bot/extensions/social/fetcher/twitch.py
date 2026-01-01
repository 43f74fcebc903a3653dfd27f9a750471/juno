from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from aiohttp import ClientSession
from discord.utils import as_chunks, utcnow
from pydantic import BaseModel, ConfigDict, Field
from yarl import URL

from bot.core import Context
from config import config


class TwitchStream(BaseModel):
    id: int
    user_id: int
    username: str = Field(alias="user_name")
    game_name: str
    type: str
    title: str
    viewer_count: int
    started_at: datetime
    language: str
    thumbnail: str = Field(alias="thumbnail_url")
    tags: List[str]
    is_mature: bool

    def __str__(self) -> str:
        return self.title

    @property
    def url(self) -> str:
        return f"https://twitch.tv/{self.username}"

    @property
    def hyperlink(self) -> str:
        return f"[`@{self.username}`]({self.url})"

    @property
    def thumbnail_url(self) -> str:
        return (
            self.thumbnail.replace("{width}", "1920").replace("{height}", "1080")
            + f"?t={utcnow().timestamp()}"
        )

    @classmethod
    async def fetch(
        cls,
        session: ClientSession,
        user_ids: List[int | str],
    ) -> List[TwitchStream]:
        """Fetch multiple streams from their user ids."""

        access_token = await config.api.twitch.get_token(session)
        streams: List[TwitchStream] = []

        for chunk in as_chunks(user_ids, 100):
            query = ""
            for user_id in chunk:
                key = "user_id" if isinstance(user_id, int) else "user_login"
                query += f"{key}={user_id}&"

            async with session.get(
                URL.build(
                    scheme="https",
                    host="api.twitch.tv",
                    path="/helix/streams",
                ).with_query(query[:-1]),
                headers={
                    "Client-ID": config.api.twitch.client_id,
                    "Authorization": f"Bearer {access_token}",
                },
            ) as response:
                if not response.ok:
                    continue

                data = await response.json()
                streams.extend([cls.parse_obj(stream) for stream in data["data"]])

        return streams


class TwitchUser(BaseModel):
    model_config = ConfigDict(coerce_numbers_to_str=True)
    id: str
    username: str = Field(alias="login")
    display_name: str
    type: str
    broadcaster_type: str
    description: str
    profile_image_url: str
    offline_image_url: str
    view_count: int
    email: Optional[str] = None
    created_at: datetime

    def __str__(self) -> str:
        return self.display_name

    @property
    def url(self) -> str:
        return f"https://twitch.tv/{self.username}"

    @property
    def hyperlink(self) -> str:
        return f"[`@{self.username}`]({self.url})"

    @property
    def avatar_url(self) -> str:
        return self.profile_image_url + f"?t={utcnow().timestamp()}"

    @property
    def offline_image(self) -> str:
        if not self.offline_image_url:
            return ""

        return self.offline_image_url + f"?t={utcnow().timestamp()}"

    async def stream(self, session: ClientSession) -> Optional[TwitchStream]:
        streams = await TwitchStream.fetch(session, [int(self.id)])
        return streams[0] if streams else None

    @classmethod
    async def fetch(
        cls,
        session: ClientSession,
        user_ids: List[int | str],
    ) -> List[TwitchUser]:
        """Fetch multiple users from their user ids."""

        access_token = await config.api.twitch.get_token(session)
        users: List[TwitchUser] = []

        for chunk in as_chunks(user_ids, 100):
            query = ""
            for user_id in chunk:
                key = "id" if isinstance(user_id, int) else "login"
                query += f"{key}={user_id}&"

            async with session.get(
                URL.build(
                    scheme="https",
                    host="api.twitch.tv",
                    path="/helix/users",
                ).with_query(query[:-1]),
                headers={
                    "Client-ID": config.api.twitch.client_id,
                    "Authorization": f"Bearer {access_token}",
                },
            ) as response:
                if not response.ok:
                    continue

                data = await response.json()
                users.extend([cls.parse_obj(user) for user in data["data"]])

        return users

    @classmethod
    async def convert(cls, ctx: Context, argument: str) -> TwitchUser:
        async with ctx.typing():
            users = await cls.fetch(ctx.session, [argument])
            if not users:
                raise ValueError(f"No Twitch user found for `{argument}`")

            return users[0]
