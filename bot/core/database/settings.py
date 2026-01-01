from __future__ import annotations

from typing import TYPE_CHECKING, Optional, TypedDict, cast

from cashews import cache
from discord import Guild, Role, TextChannel, Thread
import zon

if TYPE_CHECKING:
    from bot.core import Juno

__all__ = ("Settings",)


class Record(TypedDict):
    guild_id: int
    prefixes: list[str]
    google_safe_search: bool
    reassign_roles: bool
    welcome_removal: bool
    system_boost_removal: bool

    # Role settings
    booster_role_base_id: Optional[int]
    mute_role_id: Optional[int]
    jail_role_id: Optional[int]
    lockdown_role_id: Optional[int]
    booster_role_include: list[int]
    reassign_ignored_roles: list[int]

    # Channel settings
    jail_channel_id: Optional[int]
    publisher_channels: list[int]
    lockdown_ignore: list[int]
    monitored_threads: list[int]


class Settings:
    bot: Juno
    guild: Guild
    record: Record

    def __init__(self, bot: Juno, guild: Guild, record: Record) -> None:
        self.bot = bot
        self.guild = guild
        self.record = dict(record)  # type: ignore

    def __repr__(self) -> str:
        return f"<Settings prefixes={self.prefixes!r}>"

    @property
    def prefixes(self) -> list[str]:
        return self.record["prefixes"]

    @property
    def google_safe_search(self) -> bool:
        return self.record["google_safe_search"]

    @property
    def reassign_roles(self) -> bool:
        return self.record["reassign_roles"]

    # Role settings
    @property
    def booster_role_base(self) -> Optional[Role]:
        return self.guild.get_role(self.record["booster_role_base_id"] or 0)

    @property
    def mute_role(self) -> Optional[Role]:
        return self.guild.get_role(self.record["mute_role_id"] or 0)

    @property
    def jail_role(self) -> Optional[Role]:
        return self.guild.get_role(self.record["jail_role_id"] or 0)

    @property
    def lockdown_role(self) -> Role:
        return (
            self.guild.get_role(self.record["lockdown_role_id"] or 0)
            or self.guild.default_role
        )

    @property
    def booster_role_include(self) -> list[Role]:
        return [
            role
            for role_id in self.record["booster_role_include"]
            if (role := self.guild.get_role(role_id)) is not None
        ]

    @property
    def reassign_ignored_roles(self) -> list[Role]:
        return [
            role
            for role_id in self.record["reassign_ignored_roles"]
            if (role := self.guild.get_role(role_id)) is not None
        ]

    # Channel settings
    @property
    def jail_channel(self) -> Optional[TextChannel]:
        return cast(
            Optional[TextChannel],
            self.guild.get_channel(self.record["jail_channel_id"] or 0),
        )

    @property
    def publisher_channels(self) -> list[TextChannel]:
        return [
            channel
            for channel_id in self.record["publisher_channels"]
            if (channel := self.guild.get_channel(channel_id))
            and isinstance(channel, TextChannel)
        ]

    @property
    def lockdown_ignore(self) -> list[TextChannel]:
        return [
            channel
            for channel_id in self.record["lockdown_ignore"]
            if (channel := self.guild.get_channel(channel_id))
            and isinstance(channel, TextChannel)
        ]

    @property
    def monitored_threads(self) -> list[Thread]:
        return [
            thread
            for thread_id in self.record["monitored_threads"]
            if (thread := self.guild.get_thread(thread_id))
        ]

    @staticmethod
    def _default_config() -> Record:
        return {
            "guild_id": 0,
            "prefixes": [],
            "google_safe_search": True,
            "reassign_roles": False,
            "welcome_removal": False,
            "system_boost_removal": False,
            # Role settings
            "booster_role_base_id": 0,
            "mute_role_id": 0,
            "jail_role_id": 0,
            "lockdown_role_id": 0,
            "booster_role_include": [],
            "reassign_ignored_roles": [],
            # Channel settings
            "jail_channel_id": 0,
            "publisher_channels": [],
            "lockdown_ignore": [],
            "monitored_threads": [],
        }

    @classmethod
    def schema(cls) -> zon.ZonRecord:
        return zon.record(
            {
                "prefixes": zon.element_list(zon.string()),
                "google_safe_search": zon.boolean(),
                "reassign_roles": zon.boolean(),
                "welcome_removal": zon.boolean(),
                "system_boost_removal": zon.boolean(),
                
                # Role settings
                "booster_role_base_id": zon.number().optional(),
                "mute_role_id": zon.number().optional(),
                "jail_role_id": zon.number().optional(),
                "lockdown_role_id": zon.number().optional(),
                "booster_role_include": zon.element_list(zon.number()),
                "reassign_ignored_roles": zon.element_list(zon.number()),
                # Channel settings
                "jail_channel_id": zon.number().optional(),
                "publisher_channels": zon.element_list(zon.number()),
                "lockdown_ignore": zon.element_list(zon.number()),
                "monitored_threads": zon.element_list(zon.number()),
            }
        )

    @classmethod
    @cache(ttl="30m", key="settings:{guild.id}")
    async def fetch(cls, bot: Juno, guild: Guild) -> Settings:
        query = "SELECT * FROM settings WHERE guild_id = $1"
        record = (
            cast(Optional[Record], await bot.db.fetchrow(query, guild.id))
            or cls._default_config()
        )
        return Settings(bot, guild, record)

    async def upsert(self, revalidate: bool = True, **kwargs) -> Settings:
        columns = list(Record.__annotations__.keys())
        query = f"""
        INSERT INTO settings ({", ".join(columns)})
        VALUES ({", ".join(f"${i + 1}" for i in range(len(columns)))})
        ON CONFLICT (guild_id)
        DO UPDATE SET
            {", ".join(f"{column} = EXCLUDED.{column}" for column in columns[1:])}
        RETURNING *
        """

        record = await self.bot.db.fetchrow(
            query,
            self.guild.id,
            *[kwargs.get(column, self.record[column]) for column in columns[1:]],
        )
        self.record = dict(record)  # type: ignore
        if revalidate:
            await cache.delete(f"settings:{self.guild.id}")

        return self
