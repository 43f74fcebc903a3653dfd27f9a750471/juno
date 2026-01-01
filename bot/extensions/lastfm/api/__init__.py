from __future__ import annotations

import asyncio
from hashlib import md5
from logging import getLogger
from random import choice
from typing import List, Literal

from aiohttp import ClientSession, ClientTimeout
from aiohttp_proxy import ProxyConnector
from yarl import URL

from bot.core import Context
from config import config

from .album import Album
from .artist import Artist
from .track import Track
from .user import User

logger = getLogger("bot.lastfm")

Period = Literal["overall", "7day", "1month", "3month", "6month", "12month"]


class Timeframe:
    period: Period

    def __init__(self, period: Period):
        self.period = period

    def __str__(self) -> str:
        if self.period == "7day":
            return "weekly"

        elif self.period == "1month":
            return "monthly"

        elif self.period == "3month":
            return "past 3 months"

        elif self.period == "6month":
            return "past 6 months"

        elif self.period == "12month":
            return "yearly"

        return "overall"

    @property
    def current(self) -> str:
        if self.period == "7day":
            return "week"

        elif self.period == "1month":
            return "month"

        elif self.period == "3month":
            return "3 months"

        elif self.period == "6month":
            return "6 months"

        elif self.period == "12month":
            return "year"

        return "overall"

    @classmethod
    async def convert(cls, ctx: Context, argument: str) -> "Timeframe":
        if argument in {"weekly", "week", "1week", "7days", "7day", "7ds", "7d"}:
            return cls("7day")

        elif argument in {
            "monthly",
            "month",
            "1month",
            "1m",
            "30days",
            "30day",
            "30ds",
            "30d",
        }:
            return cls("1month")

        elif argument in {
            "3months",
            "3month",
            "3ms",
            "3m",
            "90days",
            "90day",
            "90ds",
            "90d",
        }:
            return cls("3month")

        elif argument in {
            "halfyear",
            "6months",
            "6month",
            "6mo",
            "6ms",
            "6m",
            "180days",
            "180day",
            "180ds",
            "180d",
        }:
            return cls("6month")

        elif argument in {
            "yearly",
            "year",
            "yr",
            "1year",
            "1y",
            "12months",
            "12month",
            "12mo",
            "12ms",
            "12m",
            "365days",
            "365day",
            "365ds",
            "365d",
        }:
            return cls("12month")

        return cls("overall")


class LastfmClient:
    session: ClientSession
    key_pool: List[str]

    def __init__(self) -> None:
        self.session = ClientSession(
            base_url=URL.build(
                scheme="https",
                host="ws.audioscrobbler.com",
            ),
            timeout=ClientTimeout(total=15),
            connector=ProxyConnector.from_url(config.http_proxy)
            if config.http_proxy
            else None,
        )
        self.key_pool = config.api.lastfm.keys
        self.album = Album(self)
        self.artist = Artist(self)
        self.track = Track(self)
        self.user = User(self)

    @property
    def key(self) -> str:
        if not self.key_pool:
            raise ValueError("No Last.fm API keys available, try again later")

        return choice(self.key_pool)

    async def lock_key(self, key: str, duration: int) -> None:
        logger.info(f"Temporarily locking Last.fm API key {key} for {duration} seconds")
        self.key_pool.remove(key)
        try:
            await asyncio.sleep(duration)
        finally:
            self.key_pool.append(key)
            logger.info(f"Released Last.fm API key {key} back into the pool")

    async def get(
        self,
        method: str,
        **params,
    ) -> dict:
        key = params.pop("key", self.key)
        if params.get("sk") is not None:
            key = config.api.lastfm.public_key

        payload = {
            "method": method,
            "format": "json",
            "autocorrect": 1,
            "api_key": key,
            **params,
        }
        for key, value in list(payload.items()):
            if not value:
                del payload[key]
                continue

            if not isinstance(value, str):
                payload[key] = str(value)

            if key.endswith("_"):
                payload[key] = value

        signature: List[str] = []
        for key, value in sorted(payload.items()):
            if key == "format":
                continue

            signature.append(f"{key}{value}")

        signature.append(config.api.lastfm.secret_key)
        if payload["api_key"] == config.api.lastfm.public_key:
            payload["api_sig"] = md5("".join(signature).encode()).hexdigest()

        response = await self.session.get(
            "/2.0/",
            params=payload,
        )
        if response.status == 429:
            retry_after = response.headers.get("Retry-After", 60)
            await self.lock_key(key, int(retry_after))
            return await self.get(method, **params)

        data = await response.json()
        data["status"] = response.status
        return data

    async def post(
        self,
        method: str,
        **params,
    ) -> dict:
        key = params.pop("key", self.key)
        if params.get("sk") is not None:
            key = config.api.lastfm.public_key

        payload = {
            "method": method,
            "format": "json",
            "autocorrect": 1,
            "api_key": key,
            **params,
        }
        for key, value in list(payload.items()):
            if not value:
                del payload[key]
                continue

            if not isinstance(value, str):
                payload[key] = str(value)

            if key.endswith("_"):
                payload[key] = value

        signature: List[str] = []
        for key, value in sorted(payload.items()):
            if key == "format":
                continue

            signature.append(f"{key}{value}")

        signature.append(config.api.lastfm.secret_key)
        if payload["api_key"] == config.api.lastfm.public_key:
            payload["api_sig"] = md5("".join(signature).encode()).hexdigest()

        response = await self.session.post(
            "/2.0/",
            params=payload,
        )
        if response.status == 429:
            retry_after = response.headers.get("Retry-After", 60)
            await self.lock_key(key, int(retry_after))
            return await self.get(method, **params)

        data = await response.json()
        data["status"] = response.status
        return data
