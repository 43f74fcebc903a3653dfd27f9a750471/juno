from __future__ import annotations

from contextlib import suppress
from typing import TYPE_CHECKING, Literal, Optional, TypedDict, cast

from cashews import cache
from discord import (
    Guild,
    HTTPException,
    Member,
    Message,
    PartialMessage,
    Role,
    TextChannel,
    Thread,
)

from bot.core import Juno

if TYPE_CHECKING:
    from discord.types.interactions import InteractionData


class Ticket(TypedDict):
    id: str
    guild_id: int
    channel_id: int
    user_id: int


class TicketButton(TypedDict):
    id: str
    guild_id: int
    category_id: Optional[int]
    template: Optional[str]
    topic: Optional[str]


class TicketDropdownOption(TypedDict):
    id: str
    category_id: Optional[int]
    template: Optional[str]
    topic: Optional[str]


class TicketDropdown(TypedDict):
    id: str
    guild_id: int
    options: list[TicketDropdownOption]


class Record(TypedDict):
    guild_id: int
    channel_id: int
    message_id: int
    max_tickets: Optional[int]
    inactivity_timeout: Optional[int]
    staff_role_ids: list[int]
    blacklisted_ids: list[int]
    transcript_destinations: list[str | Literal["dm"]]


class Settings:
    bot: Juno
    guild: Guild
    record: Record

    def __init__(self, bot: Juno, guild: Guild, record: Record) -> None:
        self.bot = bot
        self.guild = guild
        self.record = dict(record)  # type: ignore

    def __repr__(self) -> str:
        return f"<Settings channel={self.channel and self.channel.mention or 'N/A'}>"

    def __bool__(self) -> bool:
        return bool(self.channel)

    @property
    def channel(self) -> Optional[TextChannel]:
        return cast(
            Optional[TextChannel], self.guild.get_channel(self.record["channel_id"])
        )

    @property
    def message(self) -> Optional[PartialMessage]:
        if not self.channel:
            return None

        return self.channel.get_partial_message(self.record["message_id"])

    @property
    def staff_roles(self) -> list[Role]:
        return [
            role
            for role_id in self.record["staff_role_ids"]
            if (role := self.guild.get_role(role_id))
        ]

    @property
    def blacklisted(self) -> list[Role | Member]:
        return [
            target
            for target_id in self.record["blacklisted_ids"]
            if (target := self.guild.get_role(target_id))
            or (target := self.guild.get_member(target_id))
        ]

    @property
    def transcript_destinations(self) -> list[TextChannel | Thread | Literal["dm"]]:
        destinations = []
        for destination in self.record["transcript_destinations"]:
            if destination == "dm":
                destinations.append(destination)
                continue

            channel = self.guild.get_channel_or_thread(int(destination))
            if channel:
                destinations.append(channel)

        return destinations

    def is_blacklisted(self, target: Member) -> bool:
        return target in self.blacklisted or any(
            role in target.roles for role in self.blacklisted
        )

    def is_staff(self, member: Member) -> bool:
        return member.guild_permissions.manage_channels or any(
            role in member.roles for role in self.staff_roles
        )

    async def fetch_message(self) -> Optional[Message]:
        with suppress(HTTPException):
            return await self.bot.get_or_fetch_message(
                self.record["channel_id"],
                self.record["message_id"],
            )

        return None

    async def fetch_button(self, id: str) -> Optional[TicketButton]:
        query = "SELECT * FROM tickets.button WHERE guild_id = $1 AND id = $2"
        record = cast(
            Optional[TicketButton], await self.bot.db.fetchrow(query, self.guild.id, id)
        )
        if not record:
            return None

        return dict(record)  # type: ignore

    async def fetch_dropdown(
        self, id: str, data: InteractionData
    ) -> Optional[TicketDropdownOption]:
        if not data.get("values"):
            return None

        query = "SELECT * FROM tickets.dropdown WHERE guild_id = $1 AND id = $2"
        record = cast(
            Optional[TicketDropdown],
            await self.bot.db.fetchrow(query, self.guild.id, id),
        )
        if not record:
            return None

        for option in record["options"]:
            if option["id"] == data["values"][0]:  # type: ignore
                return option

    @staticmethod
    def _default_config() -> Record:
        return {
            "guild_id": 0,
            "channel_id": 0,
            "message_id": 0,
            "max_tickets": None,
            "inactivity_timeout": None,
            "staff_role_ids": [],
            "blacklisted_ids": [],
            "transcript_destinations": [],
        }

    @classmethod
    @cache(ttl="30m", key="tickets.settings:{guild.id}")
    async def fetch(cls, bot: Juno, guild: Guild) -> Settings:
        query = "SELECT * FROM tickets.settings WHERE guild_id = $1"
        record = cast(Optional[Record], await bot.db.fetchrow(query, guild.id))
        return cls(bot, guild, record or cls._default_config())

    async def upsert(self, revalidate: bool = True, **kwargs) -> Settings:
        query = """
        INSERT INTO tickets.settings (
            guild_id,
            channel_id,
            message_id,
            max_tickets,
            inactivity_timeout,
            staff_role_ids,
            blacklisted_ids,
            transcript_destinations
        )
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
        ON CONFLICT (guild_id)
        DO UPDATE SET
            channel_id = EXCLUDED.channel_id,
            message_id = EXCLUDED.message_id,
            max_tickets = EXCLUDED.max_tickets,
            inactivity_timeout = EXCLUDED.inactivity_timeout,
            staff_role_ids = EXCLUDED.staff_role_ids,
            blacklisted_ids = EXCLUDED.blacklisted_ids,
            transcript_destinations = EXCLUDED.transcript_destinations
        RETURNING *
        """

        record = await self.bot.db.fetchrow(
            query,
            self.guild.id,
            kwargs.get("channel_id", self.record["channel_id"]),
            kwargs.get("message_id", self.record["message_id"]),
            kwargs.get("max_tickets", self.record["max_tickets"]),
            kwargs.get("inactivity_timeout", self.record["inactivity_timeout"]),
            kwargs.get("staff_role_ids", self.record["staff_role_ids"]),
            kwargs.get("blacklisted_ids", self.record["blacklisted_ids"]),
            kwargs.get(
                "transcript_destinations", self.record["transcript_destinations"]
            ),
        )
        self.record = dict(record)  # type: ignore
        if revalidate:
            await cache.delete(f"tickets.settings:{self.guild.id}")

        return self
