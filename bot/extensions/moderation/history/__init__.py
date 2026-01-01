from typing import List, Optional, cast

from discord import Embed, Member, Message, User
from discord.ext.commands import Cog, group, has_permissions, parameter
from discord.utils import format_dt

from bot.core import Context, Juno
from bot.shared.paginator import EmbedField, Paginator

from .case import Action, Case, Record  # noqa


class History(Cog):
    def __init__(self, bot: Juno) -> None:
        self.bot = bot

    @group(invoke_without_command=True)
    @has_permissions(manage_messages=True)
    async def case(
        self,
        ctx: Context,
        case: Case = parameter(
            default=Case.most_recent,
            displayed_default="recent",
            displayed_name="id",
        ),
    ) -> Message:
        """View information about a case."""

        embed = await case.embed()
        return await ctx.send(embed=embed)

    @case.command(name="delete", aliases=("del", "remove"))
    @has_permissions(manage_messages=True)
    async def case_delete(
        self, ctx: Context, case: Case = parameter(displayed_name="id")
    ) -> None:
        """Remove a case from the database."""

        query = "DELETE FROM moderation.case WHERE guild_id = $1 AND id = $2"
        await self.bot.db.execute(query, ctx.guild.id, case.id)
        return await ctx.add_check()

    @case.command(name="reason")
    @has_permissions(manage_messages=True)
    async def case_reason(
        self,
        ctx: Context,
        case: Case = parameter(
            default=Case.most_recent,
            displayed_default="recent",
            displayed_name="id",
        ),
        *,
        reason: str,
    ) -> None:
        """Update the reason for a case."""

        query = "UPDATE moderation.case SET reason = $3 WHERE guild_id = $1 AND id = $2"
        await self.bot.db.execute(query, ctx.guild.id, case.id, reason)
        return await ctx.add_check()

    @group(invoke_without_command=True)
    @has_permissions(manage_messages=True)
    async def history(self, ctx: Context, *, user: Member | User) -> Message:
        """View all moderation cases against a user."""

        query = "SELECT * FROM moderation.case WHERE guild_id = $1 AND target_id = $2"
        records = cast(
            List[Record], await self.bot.db.fetch(query, ctx.guild.id, user.id)
        )
        if not records:
            return await ctx.warn(f"No moderation cases found for {user.mention}")

        embed = Embed(title=f"Punishment History for {user}")
        fields: List[EmbedField] = []
        for record in records:
            case = Case(self.bot, record)
            information = (
                f"{format_dt(record['created_at'])} ({format_dt(record['created_at'], 'R')})"
                f"\n>>> **Moderator:** {case.partial_moderator or 'Unknown User'} [`{record['moderator_id']}`]\n"
            )
            if record["action_expiration"]:
                information += f"**Expiration:** {format_dt(record['action_expiration'])} ({format_dt(record['action_expiration'], 'R')})\n"

            elif record["updated_at"]:
                information += f"**Updated:** {format_dt(record['updated_at'])} ({format_dt(record['updated_at'], 'R')})\n"

            fields.append(
                {
                    "name": f"Case #{case.id} | {case.action}",
                    "value": information + f"**Reason:** {record['reason']}\n",
                    "inline": False,
                }
            )

        paginator = Paginator(ctx, fields, embed, per_page=3)
        return await paginator.start()

    @history.command(name="moderator", alises=("moderation", "mod"))
    @has_permissions(manage_messages=True)
    async def history_moderator(
        self, ctx: Context, *, moderator: Member | User
    ) -> Message:
        """View all moderation cases a moderator has issued."""

        query = (
            "SELECT * FROM moderation.case WHERE guild_id = $1 AND moderator_id = $2"
        )
        records = cast(
            List[Record], await self.bot.db.fetch(query, ctx.guild.id, moderator.id)
        )
        if not records:
            return await ctx.warn(f"No moderation cases found for {moderator.mention}")

        embed = Embed(title=f"Moderation History for {moderator}")
        fields: List[EmbedField] = []
        for record in records:
            case = Case(self.bot, record)
            target = await case.target(partial=True)
            information = (
                f"{format_dt(record['created_at'])} ({format_dt(record['created_at'], 'R')})"
                f"\n>>> **{record['target_type'].title()}:** {target or 'Unknown'} [`{record['target_id']}`]\n"
            )
            if record["action_expiration"]:
                information += f"**Expiration:** {format_dt(record['action_expiration'])} ({format_dt(record['action_expiration'], 'R')})\n"

            elif record["updated_at"]:
                information += f"**Updated:** {format_dt(record['updated_at'])} ({format_dt(record['updated_at'], 'R')})\n"

            fields.append(
                {
                    "name": f"Case #{case.id} | {case.action}",
                    "value": information + f"**Reason:** {record['reason']}\n",
                    "inline": False,
                }
            )

        paginator = Paginator(ctx, fields, embed, per_page=3)
        return await paginator.start()

    @history.command(name="clear", aliases=("purge",))
    @has_permissions(manage_messages=True)
    async def history_clear(
        self, ctx: Context, *, user: Member | User
    ) -> Optional[Message]:
        """Remove all moderation cases against a user."""

        query = "DELETE FROM moderation.case WHERE guild_id = $1 AND target_id = $2"
        result = await self.bot.db.execute(query, ctx.guild.id, user.id)
        if result == "DELETE 0":
            return await ctx.warn(f"No moderation cases found for {user.mention}")

        return await ctx.add_check()
