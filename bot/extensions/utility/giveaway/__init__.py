from contextlib import suppress
from datetime import timedelta
from logging import getLogger
from typing import List, Optional, cast

from discord import Embed, HTTPException, Message, TextChannel
from discord.ext.commands import (
    BucketType,
    Cog,
    group,
    has_permissions,
    max_concurrency,
    parameter,
)
from discord.ext.tasks import loop

from bot.core import Context, Juno
from bot.shared.converters.time import Duration
from bot.shared.paginator import Paginator

from .model import GiveawayRecord

logger = getLogger("bot.giveaway")


class Giveaway(Cog):
    def __init__(self, bot: Juno) -> None:
        self.bot = bot

    async def cog_load(self) -> None:
        self.giveaway_task.start()
        return await super().cog_load()

    async def cog_unload(self) -> None:
        self.giveaway_task.cancel()
        return await super().cog_unload()

    @loop(seconds=15)
    async def giveaway_task(self) -> None:
        """Check for ended giveaways and pick winners."""

        query = """
            SELECT *
            FROM giveaway
            WHERE ends_at <= NOW()
            AND ended = FALSE
        """
        giveaways = [
            GiveawayRecord(bot=self.bot, **record)
            for record in await self.bot.db.fetch(query)
        ]

        scheduled_deletion: List[GiveawayRecord] = []
        for giveaway in giveaways:
            message = await giveaway.message()
            if not giveaway.channel or not message:
                scheduled_deletion.append(giveaway)
                continue

            elif not message.reactions:
                scheduled_deletion.append(giveaway)
                await giveaway.end()
                continue

            with suppress(HTTPException):
                await giveaway.draw(message)

        if scheduled_deletion:
            query = "DELETE FROM giveaway WHERE message_id = ANY($1::BIGINT[])"
            await self.bot.db.execute(
                query,
                [giveaway.message_id for giveaway in scheduled_deletion],
            )
            scheduled_deletion.clear()

    @group(
        aliases=("giveaways", "gw"),
        invoke_without_command=True,
    )
    @has_permissions(manage_messages=True)
    async def giveaway(self, ctx: Context) -> Message:
        """Grant prizes to your members."""

        return await ctx.send_help(ctx.command)

    @giveaway.command(name="start", aliases=("create", "new"))
    @max_concurrency(1, BucketType.guild, wait=True)
    @has_permissions(manage_messages=True)
    async def giveaway_start(
        self,
        ctx: Context,
        channel: Optional[TextChannel],
        duration: timedelta = parameter(
            converter=Duration(
                min=timedelta(minutes=5),
                max=timedelta(weeks=4),
            ),
        ),
        winners: Optional[int] = 1,
        *,
        prize: str,
    ) -> Optional[Message]:
        """Start a new giveaway.

        The duration must be between 15 seconds and 1 month.
        If multiple winners are specified, the prize will
        automatically contain the winners, eg: `2x nitro`."""

        channel = cast(TextChannel, channel or ctx.channel)
        if not isinstance(channel, TextChannel):
            return await ctx.warn("You can only start giveaways in text channels")

        giveaway = GiveawayRecord(
            bot=self.bot,
            guild_id=ctx.guild.id,
            channel_id=channel.id,
            creator_id=ctx.author.id,
            prize=prize,
            emoji="🎉",
            winners=min(max(1, winners or 1), 25),
            ends_at=ctx.message.created_at + duration,
        )

        message = await channel.send(
            content="🎉 **GIVEAWAY** 🎉",
            embed=giveaway.embed(),
        )
        await message.add_reaction("🎉")
        await giveaway.create(message)

        if channel == ctx.channel:
            await ctx.add_check()
            return await ctx.message.delete(delay=2)

        return await ctx.approve(
            f"Giveaway started in {channel.mention} for [`{giveaway}`]({message.jump_url})"
        )

    @giveaway.command(name="end", aliases=("stop",))
    @max_concurrency(1, BucketType.guild, wait=True)
    @has_permissions(manage_messages=True)
    async def giveaway_end(
        self,
        ctx: Context,
        giveaway: GiveawayRecord = parameter(
            default=GiveawayRecord.fallback,
        ),
    ) -> Optional[Message]:
        """End a giveaway early."""

        if giveaway.has_ended:
            return await ctx.warn("This giveaway has already ended")

        message = await giveaway.message()
        if not message:
            return await ctx.warn("The giveaway message no longer exists")

        await giveaway.draw(message)
        if message.channel == ctx.channel:
            await ctx.add_check()
            return await ctx.message.delete(delay=2)

        return await ctx.approve(
            f"Giveaway ended for [`{giveaway}`]({message.jump_url})"
        )

    @giveaway.command(name="reroll", aliases=("redraw", "draw"))
    @max_concurrency(1, BucketType.guild, wait=True)
    @has_permissions(manage_messages=True)
    async def giveaway_reroll(
        self,
        ctx: Context,
        giveaway: GiveawayRecord = parameter(
            default=GiveawayRecord.fallback,
        ),
    ) -> Optional[Message]:
        """Pick new winners for a giveaway."""

        if not giveaway.has_ended:
            return await ctx.warn("This giveaway has not ended yet")

        message = await giveaway.message()
        if not message:
            return await ctx.warn("The giveaway message no longer exists")

        await giveaway.draw(message)
        if message.channel == ctx.channel:
            await ctx.add_check()

        return await ctx.approve(
            f"Giveaway rerolled for [`{giveaway}`]({message.jump_url})"
        )

    @giveaway.command(name="entries", aliases=("entrants", "participants"))
    @max_concurrency(1, BucketType.guild, wait=True)
    @has_permissions(manage_messages=True)
    async def giveaway_entries(
        self,
        ctx: Context,
        giveaway: GiveawayRecord = parameter(
            default=GiveawayRecord.fallback,
        ),
    ) -> Message:
        """View all users who entered a giveaway."""

        message = await giveaway.message()
        if not message:
            return await ctx.warn("The giveaway message no longer exists")

        entries = await giveaway.entrants(message)
        if not entries:
            return await ctx.warn("No one has entered this giveaway")

        embed = Embed(title="Giveaway Entries")
        entries = [f"{member} [`{member.id}`]" for member in entries]
        paginator = Paginator(ctx, entries, embed)
        return await paginator.start()

    @giveaway.command(name="list")
    @has_permissions(manage_messages=True)
    async def giveaway_list(self, ctx: Context) -> Message:
        """View all giveaways in the server."""

        query = """
            SELECT *
            FROM giveaway
            WHERE guild_id = $1
            ORDER BY created_at DESC
        """
        giveaways = [
            GiveawayRecord(bot=self.bot, **record)
            for record in await self.bot.db.fetch(query, ctx.guild.id)
        ]

        if not giveaways:
            return await ctx.warn("No giveaways have been created yet")

        embed = Embed(title="Giveaways")
        giveaways = [
            (
                f"[`{giveaway}`]({giveaway.message_url})"
                + (" **(ENDED)**" if giveaway.has_ended else "")
            )
            for giveaway in giveaways
        ]
        paginator = Paginator(ctx, giveaways, embed)
        return await paginator.start()
