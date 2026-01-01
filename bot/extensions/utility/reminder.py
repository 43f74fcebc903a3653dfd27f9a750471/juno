from contextlib import suppress
from datetime import datetime, timedelta
from typing import List, TypedDict, cast

from discord import Embed, HTTPException, Message
from discord.ext.commands import Cog, check, group, parameter
from discord.utils import format_dt

from bot.core import Context, Juno, Timer
from bot.shared import Paginator
from bot.shared.converters.time import Duration
from bot.shared.formatter import shorten


class Record(TypedDict):
    id: int
    text: str
    expires_at: datetime


async def can_dm(ctx: Context) -> bool:
    try:
        await ctx.author.send()
    except HTTPException as exc:
        if exc.code == 50007:
            raise ValueError("You need to enable DMs to use this command")

    return True


class Reminder(Cog):
    def __init__(self, bot: Juno) -> None:
        self.bot = bot

    @Cog.listener()
    async def on_reminder_timer_complete(self, timer: Timer) -> None:
        user_id = int(timer.kwargs["user_id"])
        jump_url = cast(str, timer.kwargs["jump_url"])
        text = cast(str, timer.kwargs["text"])

        user = self.bot.get_user(user_id)
        if not user:
            return

        embed = Embed(
            title="Reminder",
            description=f"[Jump to Message]({jump_url})\n>>> {text}",
        )
        with suppress(HTTPException):
            await user.send(embed=embed)

    @group(aliases=("remind",), invoke_without_command=True)
    @check(can_dm)
    async def reminder(
        self,
        ctx: Context,
        duration: timedelta = parameter(
            converter=Duration(
                min=timedelta(minutes=1),
            ),
        ),
        *,
        text: str,
    ) -> Message:
        """Set a reminder for a specific duration."""

        timer = await Timer.create(
            self.bot,
            "reminder",
            ctx.message.created_at + duration,
            user_id=ctx.author.id,
            jump_url=ctx.message.jump_url,
            text=text,
        )

        return await ctx.send(
            f"Okay.. I'll remind you about that {format_dt(timer.expires_at, 'R')}"
            + (
                f"\nUse `{ctx.clean_prefix}{ctx.invoked_with} cancel {timer.id}` to cancel it early"
                if timer.id
                else ""
            )
        )

    @reminder.command(name="cancel", aliases=("delete", "remove", "del", "rm"))
    async def reminder_cancel(self, ctx: Context, id: int) -> Message:
        """Cancel a reminder via its ID.

        Use the `reminder list` command to view reminder IDs.
        """

        query = """
        DELETE FROM timer.task
        WHERE id = $1
        AND payload#>>'{kwargs,user_id}' = $2
        """
        result = await self.bot.db.execute(query, id, str(ctx.author.id))
        if result == "DELETE 0":
            return await ctx.warn("You don't have a reminder with that ID")

        return await ctx.approve(f"Reminder with ID `{id}` has been cancelled")

    @reminder.command(name="list")
    async def reminder_list(self, ctx: Context) -> Message:
        """View all your active reminders."""

        query = """
        SELECT
            id,
            expires_at,
            payload#>>'{kwargs,text}' AS text
        FROM timer.task
        WHERE payload#>>'{kwargs,user_id}' = $1
        AND expires_at > NOW()
        ORDER BY expires_at
        """
        records = cast(List[Record], await self.bot.db.fetch(query, str(ctx.author.id)))
        reminders = [
            f"`{str(record['id']).zfill(2)}` {shorten(record['text'], 24)} {format_dt(record['expires_at'], 'R')}"
            for record in records
        ]
        if not reminders:
            return await ctx.warn("You don't have any active reminders")

        embed = Embed(title="Reminders")
        paginator = Paginator(ctx, reminders, embed, counter=False)
        return await paginator.start()
