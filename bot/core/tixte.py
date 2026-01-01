from __future__ import annotations

from io import BytesIO
from logging import getLogger
from typing import TYPE_CHECKING

from tixte import Client, File

from bot.shared.formatter import human_size
from config import config

if TYPE_CHECKING:
    from bot.core import Juno

logger = getLogger("bot.tixte")


class Tixte:
    domain: str
    token: str
    client: Client

    def __init__(self, bot: Juno):
        self.domain = config.tixte.domain
        self.token = config.tixte.token
        self.client = Client(self.token, self.domain)

    @property
    def public_url(self) -> str:
        return config.tixte.public_url

    async def upload(self, path: str, buffer: BytesIO) -> str:
        """Upload a file to Tixte's storage bucket."""

        file = await self.client.upload(File(buffer, path))
        if not file.direct_url:
            raise ValueError(f"Failed to upload {path} to Tixte")

        size = human_size(buffer.getbuffer().nbytes)

        logger.debug(f"Uploaded {file.filename} ({size}) at {self.domain}")
        return file.direct_url.split("r/")[1]

    async def read(self, path: str) -> BytesIO:
        """Download a file from Tixte's storage bucket."""

        assert self.client._http.session is not None
        async with self.client._http.session.get(
            f"{self.public_url}/{path}"
        ) as response:
            if response.headers.get("CF-Cache-Status") != "HIT":
                logger.debug(f"Cache miss while reading {path}")

            buffer = await response.read()
            return BytesIO(buffer)
