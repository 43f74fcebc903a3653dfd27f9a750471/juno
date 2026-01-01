from typing import List, Optional, TypedDict, cast

from asyncpg import UniqueViolationError
from discord import Embed, Forbidden, HTTPException, Message, NotFound
from discord.ext.commands import Cog, Range, group, has_permissions

from bot.core import Context, Juno
from bot.shared.paginator import Paginator


class Record(TypedDict):
    guild_id: int
    trigger: str
    emoji: str


class PreviousReactionTrigger(Cog):
    def __init__(self, bot: Juno):
        self.bot = bot

    @Cog.listener("on_message_without_command")
    async def previous_reaction_trigger(self, ctx: Context) -> None:
        """React to the previous message of a matched trigger."""

        if not ctx.message.content:
            return

        query = """
        SELECT ARRAY_AGG(emoji)
        FROM triggers.previous_reaction
        WHERE guild_id = $1
        AND LOWER($2) LIKE '%' || LOWER(trigger) || '%'
        GROUP BY trigger
        """
        emojis = cast(
            List[str],
            await self.bot.db.fetchval(
                query,
                ctx.guild.id,
                ctx.message.content,
            )
            or [],
        )
        if not emojis:
            return

        key = f"reaction:{ctx.guild.id}:{ctx.author.id}"
        if await self.bot.redis.ratelimited(key, 1, 4):
            return

        previous_message = ctx.replied_message
        if not previous_message:
            async for previous_message in ctx.channel.history(
                limit=1, before=ctx.message
            ):
                break

        if not previous_message:
            return

        scheduled_deletion: List[str] = []
        for emoji in emojis:
            try:
                await previous_message.add_reaction(emoji)
            except NotFound:
                scheduled_deletion.append(emoji)
            except Forbidden:
                break
            except (HTTPException, TypeError):
                ...

        if scheduled_deletion:
            query = """
            DELETE FROM triggers.orevious_reaction
            WHERE guild_id = $1
            AND emoji = ANY($2::TEXT[])
            """
            await self.bot.db.execute(
                query,
                ctx.guild.id,
                scheduled_deletion,
            )

    @group(aliases=("previousreact", "prt", "pr"), invoke_without_command=True)
    @has_permissions(manage_messages=True)
    async def previousreaction(self, ctx: Context) -> Message:
        """Automatically react to the previous message of a matched trigger."""

        return await ctx.send_help(ctx.command)

    @previousreaction.command(name="add", aliases=("create", "new"))
    @has_permissions(manage_messages=True)
    async def previousreaction_add(
        self,
        ctx: Context,
        emoji: str,
        *,
        trigger: Range[str, 1, 50],
    ) -> Message:
        """Create a new previous reaction trigger."""

        try:
            await ctx.message.add_reaction(emoji)
        except (HTTPException, TypeError):
            return await ctx.warn("I couldn't add that reaction to the message")

        query = """
        SELECT COUNT(*) FROM triggers.previous_reaction
        WHERE guild_id = $1 AND trigger = LOWER($2)
        """
        records = cast(int, await self.bot.db.fetchval(query, ctx.guild.id, trigger))
        if records >= 3:
            return await ctx.warn("You can't have more than 3 reactions per trigger")

        query = """
        INSERT INTO triggers.previous_reaction (
            guild_id,
            trigger,
            emoji
        ) VALUES ($1, $2, $3)
        """
        try:
            await self.bot.db.execute(query, ctx.guild.id, trigger, emoji)
        except UniqueViolationError:
            return await ctx.warn("That trigger already exists")

        return await ctx.approve(f"Now reacting with {emoji} for `{trigger}`")

    @previousreaction.command(name="remove", aliases=("delete", "del", "rm"))
    @has_permissions(manage_messages=True)
    async def previousreaction_remove(
        self,
        ctx: Context,
        emoji: str,
        *,
        trigger: Range[str, 1, 50],
    ) -> Message:
        """Remove a previous reaction trigger."""

        query = """
        DELETE FROM triggers.previous_reaction
        WHERE guild_id = $1
        AND trigger = LOWER($2)
        AND emoji = $3
        """
        result = await self.bot.db.execute(query, ctx.guild.id, trigger, emoji)
        if result == "DELETE 0":
            return await ctx.warn(
                f"No previous reaction trigger was found for {emoji} on `{trigger}`"
            )

        return await ctx.approve(f"No longer reacting with {emoji} for `{trigger}`")

    @previousreaction.command(name="clear", aliases=("reset", "purge"))
    @has_permissions(manage_roles=True)
    async def previousreaction_clear(self, ctx: Context) -> Optional[Message]:
        """Remove all previous reaction triggers."""

        query = "DELETE FROM triggers.previous_reaction WHERE guild_id = $1"
        result = await self.bot.db.execute(query, ctx.guild.id)
        if result == "DELETE 0":
            return await ctx.warn("No previous reaction triggers have been set up")

        return await ctx.add_check()

    @previousreaction.command(name="list")
    @has_permissions(manage_messages=True)
    async def previousreaction_list(self, ctx: Context) -> Message:
        """View all previous reaction triggers in the server."""

        query = """
        SELECT trigger, ARRAY_AGG(emoji) AS emojis
        FROM triggers.previous_reaction
        WHERE guild_id = $1
        GROUP BY trigger
        """
        records = cast(List[Record], await self.bot.db.fetch(query, ctx.guild.id) or [])
        triggers = [
            f"{record['trigger']!r} - {', '.join(record['emojis'])}"  # type: ignore
            for record in records
        ]
        if not triggers:
            return await ctx.warn("No previous reaction triggers have been set up")

        embed = Embed(title="Previous Reaction Triggers")
        paginator = Paginator(ctx, triggers, embed)
        return await paginator.start()
