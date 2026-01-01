from datetime import datetime, timedelta
from typing import List, Optional, TypedDict, cast

from discord import Embed, HTTPException, Message, TextChannel
from discord.ext.commands import Cog, group, has_permissions, parameter
from discord.ext.tasks import loop
from discord.utils import format_dt
from humanfriendly import format_timespan

from bot.core import Context, Juno
from bot.shared import codeblock
from bot.shared.converters.time import Duration
from bot.shared.formatter import vowel
from bot.shared.paginator import Paginator
from bot.shared.script import Script


class Record(TypedDict):
    guild_id: int
    channel_id: int
    template: str
    interval: int
    next_run: datetime


class Schedule(Cog):
    def __init__(self, bot: Juno) -> None:
        self.bot = bot

    async def cog_load(self) -> None:
        self.schedule_dispatcher.start()
        return await super().cog_load()

    async def cog_unload(self) -> None:
        self.schedule_dispatcher.stop()
        return await super().cog_unload()

    @loop(minutes=1)
    async def schedule_dispatcher(self) -> None:
        """Dispatch scheduled messages."""

        query = """
        UPDATE system.schedule
        SET next_run = NOW() + INTERVAL '1 second' * interval
        WHERE next_run <= NOW()
        RETURNING *
        """
        records = cast(List[Record], await self.bot.db.fetch(query))

        scheduled_deletion: List[int] = []
        for record in records:
            channel = cast(
                Optional[TextChannel],
                self.bot.get_channel(record["channel_id"]),
            )
            if not channel:
                scheduled_deletion.append(record["channel_id"])
                continue

            script = Script(record["template"], [channel.guild, channel])
            try:
                await script.send(channel)
            except HTTPException as exc:
                raise exc
                scheduled_deletion.append(record["channel_id"])

        if scheduled_deletion:
            query = "DELETE FROM system.schedule WHERE channel_id = ANY($1::BIGINT[])"
            await self.bot.db.execute(query, scheduled_deletion)

    @group(
        aliases=("timer", "automessage", "automsg", "am"),
        invoke_without_command=True,
    )
    @has_permissions(manage_channels=True, manage_messages=True)
    async def schedule(self, ctx: Context) -> Message:
        """Schedule recurring messages at a set interval."""

        return await ctx.send_help(ctx.command)

    @schedule.command(name="add", aliases=("create", "new"))
    @has_permissions(manage_channels=True, manage_messages=True)
    async def schedule_add(
        self,
        ctx: Context,
        channel: TextChannel,
        interval: timedelta = parameter(
            converter=Duration(
                min=timedelta(minutes=30),
                max=timedelta(days=31),
            ),
        ),
        *,
        script: Script,
    ) -> Message:
        """Add a recurring message to a channel."""

        await script.send(channel)
        await self.bot.db.execute(
            """
            INSERT INTO system.schedule (
                guild_id,
                channel_id,
                template,
                interval,
                next_run
            ) VALUES ($1, $2, $3, $4, $5)
            ON CONFLICT (guild_id, channel_id)
            DO UPDATE SET
                template = EXCLUDED.template,
                interval = EXCLUDED.interval,
                next_run = EXCLUDED.next_run
            """,
            ctx.guild.id,
            channel.id,
            script.template,
            interval.total_seconds(),
            ctx.message.created_at + interval,
        )
        return await ctx.approve(
            f"Now dispatching {vowel(script.format)} message in {channel.mention} every {format_timespan(interval)}"
        )

    @schedule.command(name="remove", aliases=("delete", "del", "rm"))
    @has_permissions(manage_channels=True, manage_messages=True)
    async def schedule_remove(self, ctx: Context, channel: TextChannel) -> Message:
        """Remove a channel from the schedule."""

        query = "ELETE FROM system.schedule WHERE guild_id = $1 AND channel_id = $2"
        result = await self.bot.db.execute(query, ctx.guild.id, channel.id)
        if result == "DELETE 0":
            return await ctx.warn(
                f"No scheduled message was found for {channel.mention}"
            )

        return await ctx.approve(f"Removed the scheduled message in {channel.mention}")

    @schedule.command(name="view", aliases=("script", "template"))
    @has_permissions(manage_channels=True, manage_messages=True)
    async def schedule_view(self, ctx: Context, channel: TextChannel) -> Message:
        """View the scheduled message for a channel."""

        query = "SELECT * FROM system.schedule WHERE channel_id = $1"
        record = cast(Optional[Record], await self.bot.db.fetchrow(query, channel.id))
        if not record:
            return await ctx.warn(
                f"No scheduled message was found for {channel.mention}"
            )

        script = Script(record["template"], [ctx.guild, channel])
        embed = Embed(
            title=f"Scheduled Message in {channel}",
            description=codeblock(script.template),
        )
        embed.add_field(
            name="Interval",
            value="\n> ".join(
                [
                    f"Every `{format_timespan(record['interval'])}`",
                    f"Next dispatch {format_dt(record['next_run'], 'R')}",
                ]
            ),
        )

        await ctx.reply(embed=embed)
        return await script.send(ctx.channel)

    @schedule.command(name="clear", aliases=("reset", "purge"))
    @has_permissions(manage_channels=True, manage_messages=True)
    async def schedule_clear(self, ctx: Context) -> Message:
        """Remove all scheduled messages."""

        query = "DELETE FROM system.schedule WHERE guild_id = $1"
        result = await self.bot.db.execute(query, ctx.guild.id)
        if result == "DELETE 0":
            return await ctx.warn("No channels dispatching scheduled messages")

        return await ctx.approve("No longer dispatching scheduled messages")

    @schedule.command(name="list")
    @has_permissions(manage_channels=True, manage_messages=True)
    async def schedule_list(self, ctx: Context) -> Message:
        """View all channels receiving scheduled messages."""

        query = "SELECT * FROM system.schedule WHERE guild_id = $1"
        records = cast(List[Record], await self.bot.db.fetch(query, ctx.guild.id))
        channels = [
            f"{channel.mention} [`{script.format.upper()}`] every `{format_timespan(record['interval'])}`"
            for record in records
            if (channel := ctx.guild.get_channel(record["channel_id"]))
            and (script := Script(record["template"]))
        ]
        if not channels:
            return await ctx.warn("No channels are receiving scheduled messages")

        embed = Embed(title="Scheduled Messages")
        paginator = Paginator(ctx, channels, embed)
        return await paginator.start()
