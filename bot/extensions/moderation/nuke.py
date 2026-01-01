from __future__ import annotations

import asyncio
from contextlib import suppress
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any, List, Optional, TypedDict, cast

from discord import Embed, Guild, HTTPException, Message, TextChannel
from discord.ext.commands import Cog, group, has_permissions, parameter
from discord.ext.tasks import loop
from discord.utils import format_dt
from humanfriendly import format_timespan

from bot.core import Context, Juno
from bot.shared import Paginator
from bot.shared.converters import Duration

from .history.case import Action, Case

if TYPE_CHECKING:
    from discord.guild import VocalGuildChannel

channel_tables = {
    "starboard": "Starboard Channel",
    "logging": "Logging Channel",
    "sticky": "Sticky Message",
    "timer.nuke": "Scheduled Nuke",
    "gallery": "Gallery Restriction",
    "birthday.config": "Birthday Channel",
    "vanity": "Vanity Role Channel",
    "system.boost": "Boost Message",
    "system.welcome": "Welcome Message",
    "system.rejoin": "Rejoin Message",
    "system.goodbye": "Goodbye Message",
    "system.schedule": "Scheduled Message",
    "monitor.kick": "Kick Notifications",
    "monitor.twitch": "Twitch Notifications",
    "monitor.tiktok": "TikTok Notifications",
    "monitor.twitter": "Twitter Notifications",
    "monitor.tumblr": "Tumblr Notifications",
    "monitor.pinterest": "Pinterest Notifications",
    "monitor.youtube": "YouTube Notifications",
    "monitor.beatstars": "BeatStars Notifications",
    "monitor.reddit": "Reddit Notifications",
}


async def reconfigure_settings(
    bot: Juno,
    guild: Guild,
    original_channel: TextChannel,
    new_channel: TextChannel,
) -> List[str]:
    """Reconfigure server settings for a channel."""

    reconfigured: List[str] = []
    config_map: dict[str, Optional[TextChannel | VocalGuildChannel] | bool] = {
        "system_channel": guild.system_channel,
        "public_updates_channel": guild.public_updates_channel,
        "rules_channel": guild.rules_channel,
        "afk_channel": guild.afk_channel,
        "community": "COMMUNITY" in guild.features,
    }
    for attr, _channel in config_map.items():
        if _channel == original_channel:
            config_map[attr] = new_channel
            reconfigured.append(attr.replace("_", " ").title())

    BASE_QUERY = "UPDATE {table} SET channel_id = $2 WHERE channel_id = $1;"
    tasks: List[Any] = [guild.edit(**config_map)]  # type: ignore

    for table in channel_tables:
        tasks.append(
            bot.db.execute(
                BASE_QUERY.format(table=table),
                original_channel.id,
                new_channel.id,
            )
        )

    results = await asyncio.gather(*tasks)

    if results[0]:
        reconfigured.extend(
            [
                attr.replace("_", " ").title()
                for attr in config_map
                if config_map[attr] == new_channel
            ]
        )

    for table, result in zip(channel_tables, results[1:]):
        if result != "UPDATE 0":
            reconfigured.append(channel_tables[table])

    return reconfigured


class Record(TypedDict):
    guild_id: int
    channel_id: int
    interval: timedelta
    next_trigger: datetime


class Nuke(Cog):
    def __init__(self, bot: Juno) -> None:
        self.bot = bot

    async def cog_load(self) -> None:
        self.nuke_task.start()
        return await super().cog_load()

    async def cog_unload(self) -> None:
        self.nuke_task.cancel()
        return await super().cog_unload()

    @loop(minutes=5)
    async def nuke_task(self) -> None:
        query = """
        UPDATE timer.nuke
        SET next_trigger = next_trigger + interval
        WHERE next_trigger <= NOW()
        RETURNING guild_id, channel_id, interval
        """
        records = cast(List[Record], await self.bot.db.fetch(query))

        scheduled_deletion: List[Record] = []
        for record in records:
            guild = self.bot.get_guild(record["guild_id"])
            if not guild:
                scheduled_deletion.append(record)
                continue

            channel = cast(TextChannel, guild.get_channel(record["channel_id"]))
            if not channel:
                scheduled_deletion.append(record)
                continue

            try:
                new_channel = await channel.clone()
                settings = await reconfigure_settings(
                    self.bot, guild, channel, new_channel
                )
                await asyncio.gather(
                    new_channel.edit(position=channel.position),
                    channel.delete(
                        reason=f"Scheduled nuke every {format_timespan(record['interval'])}"
                    ),
                )
            except HTTPException:
                scheduled_deletion.append(record)
                continue

            embed = Embed(
                title="Channel Nuked",
                description="This channel has been nuked automatically",
            )
            if settings:
                embed.add_field(
                    name="Settings Synced",
                    value=">>> " + "\n".join(settings),
                )

            with suppress(HTTPException):
                await new_channel.send(embed=embed)

        if scheduled_deletion:
            await self.bot.db.executemany(
                """
                DELETE FROM timer.nuke
                WHERE guild_id = $1
                AND channel_id = $2
                """,
                [
                    (record["guild_id"], record["channel_id"])
                    for record in scheduled_deletion
                ],
            )

    @group(invoke_without_command=True)
    @has_permissions(administrator=True)
    async def nuke(self, ctx: Context) -> Message:
        """Clone the current channel and delete the original.

        This action is irreversible and will delete the channel.
        """

        channel = ctx.channel
        if not isinstance(channel, TextChannel):
            return await ctx.warn("This command can only be used in text channels")

        await ctx.prompt(
            "Are you sure you want to nuke this channel?",
            "This action is irreversible and will delete the channel",
        )

        new_channel = await channel.clone()
        settings = await reconfigure_settings(self.bot, ctx.guild, channel, new_channel)
        await asyncio.gather(
            new_channel.edit(position=channel.position),
            channel.delete(reason=f"Nuked by {ctx.author} ({ctx.author.id})"),
            Case.create(
                ctx, channel, Action.NUKE, f"Nuked by {ctx.author} ({ctx.author.id})"
            ),
        )

        embed = Embed(
            title="Channel Nuked",
            description=f"This channel has been nuked by {ctx.author.mention}",
        )
        if settings:
            embed.add_field(
                name="Settings Synced",
                value=">>> " + "\n".join(settings),
            )

        return await new_channel.send(embed=embed)

    @nuke.command(name="add", aliases=("create", "timer"))
    @has_permissions(administrator=True)
    async def nuke_add(
        self,
        ctx: Context,
        channel: TextChannel,
        interval: timedelta = parameter(
            converter=Duration(
                min=timedelta(hours=1),
                max=timedelta(days=7),
            ),
        ),
    ) -> Message:
        """Schedule automatic nukes for a channel."""

        await self.bot.db.execute(
            """
            INSERT INTO timer.nuke (guild_id, channel_id, interval, next_trigger)
            VALUES ($1, $2, $3, $4)
            ON CONFLICT (guild_id, channel_id)
            DO UPDATE SET
                interval = EXCLUDED.interval,
                next_trigger = EXCLUDED.next_trigger
            """,
            ctx.guild.id,
            channel.id,
            interval,
            datetime.utcnow() + interval,
        )

        return await ctx.approve(
            f"Now automatically nuking {channel.mention} after `{format_timespan(interval)}`"
        )

    @nuke.command(name="remove", aliases=("delete", "del", "rm"))
    @has_permissions(administrator=True)
    async def nuke_remove(self, ctx: Context, channel: TextChannel) -> Message:
        """Remove an automatic channel nuke."""

        query = "DELETE FROM timer.nuke WHERE guild_id = $1 AND channel_id = $2"
        result = await self.bot.db.execute(query, ctx.guild.id, channel.id)
        if result == "DELETE 0":
            return await ctx.warn("This channel is not scheduled for automatic nukes")

        return await ctx.approve(f"No longer automatically nuking {channel.mention}")

    @nuke.command(name="view")
    @has_permissions(administrator=True)
    async def nuke_view(self, ctx: Context, channel: TextChannel) -> Message:
        """View the next scheduled nuke for a channel."""

        query = "SELECT interval, next_trigger FROM timer.nuke WHERE guild_id = $1 AND channel_id = $2"
        record = cast(
            Record, await self.bot.db.fetchrow(query, ctx.guild.id, channel.id)
        )
        if not record:
            return await ctx.warn("This channel is not scheduled for automatic nukes")

        embed = Embed(
            title="Scheduled Nuke",
            description="\n> ".join(
                [
                    f"After `{format_timespan(record['interval'])}`",
                    f"Next nuke {format_dt(record['next_trigger'], 'R')}",
                ]
            ),
        )
        return await ctx.send(embed=embed)

    @nuke.command(name="clear", aliases=("reset", "purge"))
    @has_permissions(manage_channels=True, manage_messages=True)
    async def nuke_clear(self, ctx: Context) -> Message:
        """Remove all scheduled automatic nukes."""

        query = "DELETE FROM timer.nuke WHERE guild_id = $1"
        result = await self.bot.db.execute(query, ctx.guild.id)
        if result == "DELETE 0":
            return await ctx.warn("No channels are scheduled for automatic nukes")

        return await ctx.approve("No longer automatically nuking any channels")

    @nuke.command(name="list")
    @has_permissions(administrator=True)
    async def nuke_list(self, ctx: Context) -> Message:
        """View all channels scheduled for automatic nukes."""

        query = "SELECT channel_id, interval FROM timer.nuke WHERE guild_id = $1"
        records = cast(List[Record], await self.bot.db.fetch(query, ctx.guild.id))
        channels = [
            f"{channel.mention} after `{format_timespan(record['interval'])}`"
            for record in records
            if (channel := ctx.guild.get_channel(record["channel_id"]))
        ]
        if not channels:
            return await ctx.warn("No channels are scheduled for automatic nukes")

        embed = Embed(title="Scheduled Nukes")
        paginator = Paginator(ctx, channels, embed)
        return await paginator.start()
