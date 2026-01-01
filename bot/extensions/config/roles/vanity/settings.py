from __future__ import annotations

from typing import List, Optional, TypedDict, cast

from cashews import cache
from discord import (
    Guild,
    HTTPException,
    Member,
    Message,
    Role,
    Status,
    TextChannel,
    Thread,
)

from bot.core import Juno
from bot.shared.script import Script

__all__ = ("Settings",)


class Record(TypedDict):
    guild_id: int
    channel_id: Optional[int]
    role_ids: List[int]
    template: Optional[str]


class Settings:
    bot: Juno
    guild: Guild
    record: Record

    def __init__(self, bot: Juno, guild: Guild, record: Record) -> None:
        self.bot = bot
        self.guild = guild
        self.record = dict(record)  # type: ignore

    def __repr__(self) -> str:
        return f"<Settings channel_id={self.channel_id} role_ids={self.role_ids} template={self.template}>"

    def __bool__(self) -> bool:
        return bool(self.roles)

    @property
    def channel_id(self) -> Optional[int]:
        return self.record.get("channel_id")

    @property
    def role_ids(self) -> List[int]:
        return self.record.get("role_ids", [])

    @property
    def template(self) -> Optional[str]:
        return self.record.get("template")

    @property
    def roles(self) -> List[Role]:
        return [
            role
            for role_id in self.role_ids
            if (role := self.guild.get_role(role_id)) and role.is_assignable()
        ]

    @property
    def channel(self) -> Optional[TextChannel | Thread]:
        return cast(
            Optional[TextChannel | Thread],
            self.guild.get_channel_or_thread(self.channel_id or 0),
        )

    @staticmethod
    def _default_config() -> Record:
        return {
            "guild_id": 0,
            "channel_id": None,
            "role_ids": [],
            "template": None,
        }

    @property
    def _default_template(self) -> str:
        return "\n".join(
            [
                "{title: Vanity Notification}",
                "{description: Thank you {user.mention}}",
                "{footer: Add /{vanity} to your status for the {role} role}",
            ]
        )

    async def dispatch_notification(
        self,
        before: Member,
        member: Member,
    ) -> Optional[Message]:
        if not self.channel:
            return

        elif before.status == Status.offline or before.status != member.status:
            return

        key = f"vanity.notification:{self.guild.id}-{member.id}"
        ratelimited = await self.bot.redis.ratelimited(key, limit=1, timespan=3 * 3600)
        if ratelimited:
            return

        vanity_block = self.guild.vanity_url_code or self.guild.name
        script = Script(
            self.template or self._default_template,
            [
                self.guild,
                self.channel,
                member,
                self.roles[0],
                ("vanity", vanity_block),
            ],
        )
        try:
            await script.send(self.channel)
        except HTTPException:
            await self.upsert(template=None)

    @classmethod
    @cache(ttl="30m", key="vanity.settings:{guild.id}")
    async def fetch(cls, bot: Juno, guild: Guild) -> Settings:
        query = "SELECT * FROM vanity WHERE guild_id = $1"
        record = (
            cast(Optional[Record], await bot.db.fetchrow(query, guild.id))
            or cls._default_config()
        )

        return cls(bot, guild, record)

    async def upsert(self, revalidate: bool = True, **kwargs) -> Settings:
        query = """
        INSERT INTO vanity (
            guild_id,
            channel_id,
            role_ids,
            template
        )
        VALUES ($1, $2, $3, $4)
        ON CONFLICT (guild_id)
        DO UPDATE SET
            channel_id = EXCLUDED.channel_id,
            role_ids = EXCLUDED.role_ids,
            template = EXCLUDED.template
        RETURNING *
        """

        record = await self.bot.db.fetchrow(
            query,
            self.guild.id,
            kwargs.get("channel_id", self.channel_id),
            kwargs.get("role_ids", self.role_ids),
            kwargs.get("template", self.template),
        )
        self.record = dict(record)  # type: ignore
        if revalidate:
            await cache.delete(f"vanity.settings:{self.guild.id}")

        return self
