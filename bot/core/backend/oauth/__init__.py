from __future__ import annotations

import functools
import secrets
from contextlib import suppress
from datetime import datetime, timedelta
from logging import getLogger
from typing import (
    TYPE_CHECKING,
    Awaitable,
    Callable,
    List,
    Optional,
    ParamSpec,
    TypeVar,
    cast,
)

import discord
from aiohttp import ClientSession
from aiohttp.web import Request, Response, StreamResponse, json_response, middleware
from cashews import cache
from discord.utils import utcnow
from pydantic import BaseModel, ValidationError

from bot.types.config import OAuth2Config
from config import config

from .interfaces import OAuthGuild, OAuthUser

if TYPE_CHECKING:
    from bot.core import Juno

    from .. import Backend

logger = getLogger("bot.backend.oauth")


class OAuthRequest(Request):
    authorization: Authorization
    guild: discord.Guild


class Authorization(BaseModel):
    user_id: int = 0
    access_token: str
    token_type: str = "Bearer"
    refresh_token: str
    expires_in: float = 0
    scope: str = "identify"

    @property
    def expires_at(self) -> datetime:
        return utcnow() + timedelta(seconds=self.expires_in)

    @property
    def expired(self) -> bool:
        return self.expires_at <= utcnow()


class OAuth:
    bot: Juno
    config: OAuth2Config
    session: ClientSession
    scopes: list[str] = ["identify", "guilds"]

    def __init__(self, bot: Juno):
        self.bot = bot
        self.config = config.oauth
        self.session = ClientSession()

    @property
    def login_url(self) -> str:
        scope = "+".join(self.scopes)
        return f"https://discord.com/api/v9/oauth2/authorize?client_id={self.config.client_id}&redirect_uri={self.config.redirect_uri}&response_type=code&scope={scope}"

    async def authorize(self, code: str) -> Optional[Authorization]:
        async with self.session.post(
            "https://discord.com/api/v9/oauth2/token",
            data={
                "client_id": self.config.client_id,
                "client_secret": self.config.client_secret,
                "redirect_uri": self.config.redirect_uri,
                "grant_type": "authorization_code",
                "code": code,
            },
        ) as response:
            data = await response.json()
            with suppress(ValidationError):
                authorization = Authorization(**data)
                if not all(scope in authorization.scope for scope in self.scopes):
                    logger.warning(
                        f"Received invalid scopes for {authorization.access_token[:8]}.."
                    )
                    return None

                return authorization

        return None

    async def refresh(self, authorization: Authorization) -> Optional[Authorization]:
        refresh_token = authorization.refresh_token
        async with self.session.post(
            "https://discord.com/api/v9/oauth2/token",
            data={
                "client_id": self.config.client_id,
                "client_secret": self.config.client_secret,
                "redirect_uri": self.config.redirect_uri,
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
            },
        ) as response:
            data = await response.json()
            with suppress(ValidationError):
                authorization = Authorization(**data, user_id=authorization.user_id)
                await self.bot.db.execute(
                    """
                    UPDATE oauth
                    SET
                        access_token = $2,
                        refresh_token = $3,
                        expires_at = $4
                    WHERE refresh_token = $1
                    """,
                    refresh_token,
                    authorization.access_token,
                    authorization.refresh_token,
                    authorization.expires_at,
                )

                return authorization

        return None

    async def request(
        self,
        method: str,
        endpoint: str,
        authorization: Authorization,
        **kwargs,
    ) -> dict:
        if authorization.expired:
            refreshed = await self.refresh(authorization)
            if not refreshed:
                raise ValueError("Failed to refresh token")

            authorization = refreshed

        async with self.session.request(
            method,
            f"https://discord.com/api/v9{endpoint}",
            headers={"Authorization": f"Bearer {authorization.access_token}"},
            **kwargs,
        ) as response:
            return await response.json()

    @cache(ttl="30m", key="{authorization.access_token}", prefix="identity")
    async def identify(self, authorization: Authorization) -> Optional[OAuthUser]:
        with suppress(ValidationError, ValueError, KeyError):
            data = await self.request("GET", "/users/@me", authorization)
            return OAuthUser(**data)

    @cache(ttl="60s", key="{authorization.access_token}", prefix="guilds")
    async def guilds(self, authorization: Authorization) -> List[OAuthGuild]:
        with suppress(ValidationError, ValueError, KeyError):
            data = await self.request("GET", "/users/@me/guilds", authorization)
            guilds: List[OAuthGuild] = []
            for partial_guild in data:
                guild = self.bot.get_guild(int(partial_guild["id"]))
                guilds.append(OAuthGuild(**partial_guild, bot=guild is not None))

            return guilds

        return []

    async def get_session(self, token: str) -> Optional[Authorization]:
        query = "SELECT * FROM oauth WHERE token = $1"
        record = await self.bot.db.fetchrow(query, token)
        if not record:
            return None

        authorization = Authorization(
            **record.to_dict(),
            expires_in=(record["expires_at"] - utcnow()).total_seconds(),
        )
        if not authorization:
            return None

        return authorization

    async def create_session(self, data: Authorization, user: OAuthUser) -> str:
        token = secrets.token_urlsafe(64)
        await self.bot.db.execute(
            """
            INSERT INTO oauth (
                user_id,
                username,
                token,
                access_token,
                refresh_token,
                expires_at
            ) VALUES ($1, $2, $3, $4, $5, $6)
            ON CONFLICT (user_id) DO UPDATE SET
                token = EXCLUDED.token,
                access_token = EXCLUDED.access_token,
                refresh_token = EXCLUDED.refresh_token,
                expires_at = EXCLUDED.expires_at
            """,
            user.id,
            user.username,
            token,
            data.access_token,
            data.refresh_token,
            data.expires_at,
        )

        return token

    async def close(self):
        await self.session.close()


T = TypeVar("T", bound=Response)
P = ParamSpec("P")


@middleware
async def auth_middleware(
    request: Request,
    handler: Callable[[Request], Awaitable[StreamResponse]],
) -> StreamResponse:
    if request.method == "OPTIONS":
        return json_response({}, headers={
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods": "GET, POST, PUT, DELETE",
            "Access-Control-Allow-Headers": "Authorization, Content-Type",
            "Access-Control-Max-Age": "86400",
        })
    
    if not request.path.startswith(("/@me", "/@auth")) or request.path in (
        "/@me/login",
        "/@me/callback",
    ):
        logger.debug(f"Authenication for {request.path} is not required")
        return await handler(request)

    logger.debug(f"Authenication for {request.path} is required")
    token = request.cookies.get("token") or request.headers.get("Authorization")
    if not token:
        return json_response({"error": "Unauthorized"}, status=401)

    oauth = cast(OAuth, request.app["oauth"])
    session = await oauth.get_session(token)
    if not session:
        return json_response({"error": "Unauthorized"}, status=401)

    request.authorization = session
    if "guild_id" in request.match_info:
        try:
            guild_id = int(request.match_info["guild_id"])
        except ValueError:
            return json_response({"error": "Invalid guild_id"}, status=400)

        guild = oauth.bot.get_guild(guild_id)
        if not guild:
            return json_response({"error": "Guild not found"}, status=404)

        request.guild = guild

    return await handler(request)


def has_permissions(*permissions: str):
    def decorator(func: Callable[..., Awaitable[T]]):
        @functools.wraps(func)
        async def wrapper(
            self: Backend,
            request: OAuthRequest,
            *args,
            **kwargs,
        ) -> T | Response:
            guild_id = int(request.match_info["guild_id"])
            session = request.authorization
            guild = self.bot.get_guild(guild_id)
            if not guild:
                return json_response({"error": "Guild not found"}, status=404)

            member = guild.get_member(session.user_id)
            if not member:
                return json_response({"error": "Unauthorized"}, status=401)

            missing = [
                perm
                for perm in permissions
                if not getattr(member.guild_permissions, perm)
            ]
            if missing:
                return json_response(
                    {"error": "Missing necessary permissions", "permissions": missing},
                    status=403,
                )

            return await func(self, request, *args, **kwargs)

        return wrapper

    return decorator
