from datetime import datetime
from io import BytesIO
from logging import getLogger
from typing import List, Optional

import discord
from pydantic import BaseModel

from bot.core import Juno

logger = getLogger("coomer.cdn")


class Attachment(BaseModel):
    name: Optional[str] = None
    path: Optional[str] = None

    @property
    def extension(self) -> str:
        if not self.path:
            return ""

        return self.path.split(".")[-1]

    @property
    def url(self) -> str:
        if not self.path:
            return ""

        return f"https://img.coomer.su/thumbnail/data{self.path}"

    async def read(self, bot: Juno) -> discord.File:
        response = await bot.session.get(self.url)
        buffer = await response.read()
        return discord.File(BytesIO(buffer), filename=self.name)


class Post(BaseModel):
    id: str
    user: str
    service: str
    title: str
    content: str
    embed: Optional[dict] = None
    shared_file: bool
    added: datetime
    published: datetime
    edited: Optional[datetime] = None
    attachments: Optional[List[Attachment]] = None
    file: Optional[Attachment] = None

    @property
    def url(self) -> str:
        return f"https://coomer.su/{self.service}/user/{self.user}/post/{self.id}"

    @property
    def files(self) -> List[Attachment]:
        if not self.file:
            return []

        return [self.file]
