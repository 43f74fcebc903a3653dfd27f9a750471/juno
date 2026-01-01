import asyncio
from asyncio import Task
from collections import defaultdict
from typing import Any, List, Optional, TypedDict, cast

from discord import TextChannel, Thread

from bot.core import Juno
from bot.shared import retry


class Record(TypedDict):
    guild_id: int
    channel_id: int
    username: str
    user_id: str
    template: Optional[str]
    channel: TextChannel | Thread


class Watcher:
    bot: Juno
    scheduler: Task
    interval: float
    sleep: float
    background: bool
    scheduled_deletion: List[Record]

    def __init__(self, bot: Juno, *, interval: float = 60, sleep: float = 0, background: bool = True) -> None:
        self.bot = bot
        self.interval = interval
        self.sleep = sleep
        self.background = background
        self.scheduled_deletion = list()
        self.scheduler = asyncio.create_task(self.schedule(interval), name=f"{self.name} scheduler")

    def __str__(self) -> str:
        return self.name

    def __repr__(self) -> str:
        return f"<{self.name} key={self.key!r}>"

    @property
    def name(self) -> str:
        return self.__class__.__name__

    @property
    def table(self) -> str:
        return f"monitor.{self.name.lower()}"

    @property
    def key(self) -> str:
        return f"watcher:{self.name.lower()}"

    async def schedule(self, interval: float) -> None:
        while True:
            await self.monitor()
            await asyncio.sleep(interval)

    async def monitor(self) -> None:
        records = await self.get_records()
        for user_id, records in records.items():
            if self.background:
                self.bot.loop.create_task(self.check(user_id, records))
            else:
                await self.check(user_id, records)
                if self.sleep:
                    await asyncio.sleep(self.sleep)

        # if self.scheduled_deletion:
        #     await self.bot.db.executemany(
        #         f"DELETE FROM {self.table} WHERE channel_id = $1 AND username = $2",
        #         [
        #             (record["channel_id"], record["username"])
        #             for record in self.scheduled_deletion
        #         ],
        #     )
        #     self.scheduled_deletion.clear()

    @retry(attempts=6, delay=30)
    async def get_records(self) -> dict[str, List[Record]]:
        query = f"SELECT * FROM {self.table}"
        records = cast(List[Record], await self.bot.db.fetch(query))

        output: dict[str, List[Record]] = defaultdict(list)
        for record in records:
            if record in self.scheduled_deletion:
                continue

            record = cast(Record, dict(record))
            channel = self.get_channel(record)
            if not channel:
                self.scheduled_deletion.append(record)
                continue

            record["channel"] = channel
            output[record["user_id"]].append(record)

        return output

    def get_channel(self, record: Record) -> Optional[TextChannel | Thread]:
        guild = self.bot.get_guild(record["guild_id"])
        if not guild:
            return None

        channel = guild.get_channel_or_thread(record["channel_id"])
        if not isinstance(channel, (TextChannel, Thread)) or not (
            channel.permissions_for(channel.guild.me).send_messages
            and channel.permissions_for(channel.guild.me).embed_links
            and channel.permissions_for(channel.guild.me).attach_files
        ):
            return None

        return channel

    async def check(self, user_id: str, records: List[Record]) -> None:
        raise NotImplementedError

    async def dispatch(self, post: Any, records: List[Record]) -> None:
        raise NotImplementedError
