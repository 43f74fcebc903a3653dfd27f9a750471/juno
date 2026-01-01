from __future__ import annotations
from fastapi import FastAPI
from aiohttp import ClientSession, TCPConnector, AsyncResolver, CookieJar
from pathlib import Path
from urllib.parse import urlparse, unquote as url_unescape
from typing import TYPE_CHECKING, Optional
from pydantic import BaseModel, Field, computed_field
from .browser import BrowserManager
from os import environ as env
from xxhash import xxh32_hexdigest
from cashews import cache
from collections import defaultdict
from ujson import loads
import asyncio
import sys

if TYPE_CHECKING:
    from . import Media

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/93.0.4577.63 Safari/537.36",
}

class Media(BaseModel):
    filename: str
    original_url: str = Field(exclude=True)
    kwargs: dict = Field(default_factory=dict, exclude=True)

    @computed_field
    @property
    def url(self) -> str:
        return f"{services.base_url}/media/{self.filename}"

class Services:
    app: FastAPI
    session: ClientSession
    browser: BrowserManager
    passive_cache: dict[str, Media] = {}  # redistribution cache
    lock: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)

    async def setup(self, app: FastAPI):
        cache.setup("mem://")
        self.app = app
        self.session = ClientSession(
            cookies=CookieJar(),
            headers=DEFAULT_HEADERS,
            connector=TCPConnector(resolver=AsyncResolver()),
        )
        self.browser = await BrowserManager().setup()

    @property
    def base_url(self) -> str:
        return env.get("BASE_URL", "http://127.0.0.1:1337")

    @property
    def keys(self) -> dict[str, str]:
        return loads(open("keys.json", "r").read())

    @property
    def cache(self):
        return cache

    async def close(self):
        await self.session.close()

    def passive_save(
        self,
        url: str,
        prefix: str,
        filename: Optional[str] = None,
        extension: Optional[str] = None,
        **kwargs,
    ) -> Media:
        filename = filename or Path(urlparse(url_unescape(url)).path).name
        if not extension:
            extension = (
                Path(filename).suffix.replace("heic", "jpg").replace("jpeg", "jpg")
            )
        else:
            extension = "." + extension.lstrip(".")

        filename = f"{prefix}{xxh32_hexdigest(filename)}{extension}"
        media = Media(filename=filename, original_url=url, kwargs=kwargs)
        self.passive_cache[filename] = media
        return media


services = Services()
