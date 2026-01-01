from datetime import datetime
from logging import getLogger
from typing import List, TypedDict, cast

from discord import Embed, Member, Message, User
from discord.ext.commands import Cog, group, parameter
from discord.ext.tasks import loop
from discord.utils import format_dt, utcnow

from bot.core import Context, Juno
from bot.shared import Paginator
from bot.shared.formatter import plural


class Record(TypedDict):
    user_id: int
    username: str
    is_nickname: bool
    timestamp: datetime


logger = getLogger("bot.utility")
batch: List[Record] = []


class NameHistory(Cog):
    def __init__(self, bot: Juno) -> None:
        self.bot = bot

    async def cog_load(self) -> None:
        self.namehistory_push.start()
        return await super().cog_load()

    async def cog_unload(self) -> None:
        self.namehistory_push.cancel()
        return await super().cog_unload()

    @loop(seconds=30)
    async def namehistory_push(self):
        if not batch:
            return

        query = "INSERT INTO name_history (user_id, username, is_nickname, timestamp) VALUES ($1, $2, $3, $4) ON CONFLICT DO NOTHING"
        await self.bot.db.executemany(
            query,
            [
                (
                    record["user_id"],
                    record["username"],
                    record["is_nickname"],
                    record["timestamp"],
                )
                for record in batch
            ],
        )

        logger.debug(f"Pushed {plural(len(batch)):record} to the database")
        batch.clear()

    @Cog.listener("on_user_update")
    async def namehistory_save(self, before: User, after: User) -> None:
        if before.name == after.name:
            return

        batch.append(
            {
                "user_id": after.id,
                "username": after.name,
                "is_nickname": False,
                "timestamp": utcnow(),
            }
        )

    @Cog.listener("on_member_update")
    async def namehistory_nick_save(self, before: Member, after: Member) -> None:
        if before.nick == after.nick or not after.nick:
            return

        batch.append(
            {
                "user_id": after.id,
                "username": after.nick,
                "is_nickname": True,
                "timestamp": utcnow(),
            }
        )

    @group(aliases=("names", "nh"), invoke_without_command=True)
    async def namehistory(
        self,
        ctx: Context,
        user: Member | User = parameter(default=lambda ctx: ctx.author),
    ) -> Message:
        """View a user's name history."""

        query = "SELECT * FROM name_history WHERE user_id = $1 ORDER BY timestamp DESC"
        records = cast(List[Record], await self.bot.db.fetch(query, user.id))
        if not records:
            return await ctx.warn("The user doesn't have any name history")

        embed = Embed(title=f"Name history for {user}")
        names = [
            f"`{str(index).zfill(2)}{record['is_nickname'] * 'N' or 'U'}` {record['username']} {format_dt(record['timestamp'], 'R')}"
            for index, record in enumerate(records, start=1)
        ]

        paginator = Paginator(ctx, names, embed, counter=False)
        return await paginator.start()

    @namehistory.command(name="clear")
    async def namehistory_clear(self, ctx: Context) -> Message:
        """Remove your archived name history."""

        query = "DELETE FROM name_history WHERE user_id = $1"
        await self.bot.db.execute(query, ctx.author.id)

        return await ctx.respond("Your name history has been cleared")
