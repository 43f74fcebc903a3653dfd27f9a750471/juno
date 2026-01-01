from __future__ import annotations

from typing import TYPE_CHECKING, Optional, TypedDict, cast

from cashews import cache
from discord import Color

if TYPE_CHECKING:
    from bot.core import Juno

    from .. import Context

__all__ = ("Reskin",)


class Record(TypedDict):
    status: bool
    username: Optional[str]
    avatar_url: Optional[str]
    embed_color: Optional[int]


class Reskin:
    ctx: Context
    record: Record

    def __init__(self, ctx: Context, record: Record) -> None:
        self.ctx = ctx
        self.record = record

    def __repr__(self) -> str:
        return f"<Reskin status={self.status} username={self.username!r} avatar_url={self.avatar_url!r}>"

    @property
    def bot(self) -> Juno:
        return self.ctx.bot

    @property
    def status(self) -> bool:
        return self.record["status"]

    @property
    def username(self) -> str:
        return self.record.get("username") or self.bot.user.display_name

    @property
    def avatar_url(self) -> str:
        if self.record.get("avatar_url"):
            return f"{self.bot.tixte.public_url}/{self.record['avatar_url']}"

        return self.bot.user.display_avatar.url

    @property
    def embed_color(self) -> Color:
        if self.record["embed_color"]:
            return Color(self.record["embed_color"])

        return Color.dark_embed()

    @classmethod
    @cache(ttl="30m", key="reskin:config:{ctx.author.id}")
    async def fetch(cls, ctx: Context) -> Optional[Reskin]:
        query = "SELECT * FROM reskin.config WHERE user_id = $1"
        record = cast(Optional[Record], await ctx.bot.db.fetchrow(query, ctx.author.id))
        if record:
            return cls(ctx, record)

    @classmethod
    async def update(cls, ctx: Context, **kwargs) -> Reskin:
        query = """
        INSERT INTO reskin.config (user_id, status, username, avatar_url, embed_color)
        VALUES ($1, $2, $3, $4, $5)
        ON CONFLICT (user_id)
        DO UPDATE SET
            status = EXCLUDED.status,
            username = COALESCE(EXCLUDED.username, reskin.config.username),
            avatar_url = COALESCE(EXCLUDED.avatar_url, reskin.config.avatar_url),
            embed_color = COALESCE(EXCLUDED.embed_color, reskin.config.embed_color)
        RETURNING *
        """

        kwargs["status"] = kwargs.get("status", True)
        record = await ctx.bot.db.fetchrow(
            query,
            ctx.author.id,
            kwargs["status"],
            kwargs.get("username"),
            kwargs.get("avatar_url"),
            kwargs.get("embed_color"),
        )

        await cache.delete(f"reskin:config:{ctx.author.id}")
        return cls(ctx, record)
