from __future__ import annotations

from typing import TYPE_CHECKING, Optional, TypedDict, cast

from cashews import cache
from discord import Color, Guild

if TYPE_CHECKING:
    from bot.core import Juno

__all__ = ("GuildReskin",)


class Record(TypedDict):
    status: bool
    username: Optional[str]
    avatar_url: Optional[str]
    embed_color: Optional[int]


class GuildReskin:
    guild: Guild
    record: Record

    def __init__(self, guild: Guild, record: Record) -> None:
        self.guild = guild
        self.record = record

    def __repr__(self) -> str:
        return f"<GuildReskin status={self.status} username={self.username!r} avatar_url={self.avatar_url!r}>"

    @property
    def bot(self) -> "Juno":
        return cast("Juno", self.guild._state._get_client())

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
    @cache(ttl="30m", key="reskin:config:{guild.id}")
    async def fetch(cls, guild: Guild) -> Optional[GuildReskin]:
        bot = cast("Juno", guild._state._get_client())
        query = "SELECT * FROM reskin.guild_config WHERE guild_id = $1"
        record = cast(Optional[Record], await bot.db.fetchrow(query, guild.id))
        if record:
            return cls(guild, record)

    @classmethod
    async def update(cls, guild: Guild, **kwargs) -> GuildReskin:
        query = """
        INSERT INTO reskin.guild_config (guild_id, status, username, avatar_url, embed_color)
        VALUES ($1, $2, $3, $4, $5)
        ON CONFLICT (guild_id)
        DO UPDATE SET
            status = EXCLUDED.status,
            username = COALESCE(EXCLUDED.username, reskin.guild_config.username),
            avatar_url = COALESCE(EXCLUDED.avatar_url, reskin.guild_config.avatar_url),
            embed_color = COALESCE(EXCLUDED.embed_color, reskin.guild_config.embed_color)
        RETURNING *
        """

        kwargs["status"] = kwargs.get("status", True)
        bot = cast("Juno", guild._state._get_client())
        record = await bot.db.fetchrow(
            query,
            guild.id,
            kwargs["status"],
            kwargs.get("username"),
            kwargs.get("avatar_url"),
            kwargs.get("embed_color"),
        )

        await cache.delete(f"reskin:config:{guild.id}")
        return cls(guild, record)
