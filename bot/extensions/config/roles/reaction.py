from contextlib import suppress
from typing import Annotated, List, Optional, TypedDict, cast

from asyncpg import UniqueViolationError
from discord import Embed, HTTPException, Message, RawReactionActionEvent, Role
from discord.ext.commands import Cog, group, has_permissions

from bot.core import Context, Juno
from bot.shared.converters.role import StrictRole
from bot.shared.paginator import Paginator


class Record(TypedDict):
    guild_id: int
    channel_id: int
    message_id: int
    role_id: int
    emoji: str


class ReactionRoles(Cog):
    def __init__(self, bot: Juno):
        self.bot = bot

    @Cog.listener("on_raw_reaction_add")
    async def reactionrole_assign(self, payload: RawReactionActionEvent) -> None:
        """Assign a role to a member based on their reaction."""

        guild = self.bot.get_guild(payload.guild_id or 0)
        member = payload.member or (guild and guild.get_member(payload.user_id))
        if not guild or not member or member.bot:
            return

        query = """
        SELECT role_id FROM roles.reaction
        WHERE guild_id = $1 AND message_id = $2 AND emoji = $3
        """
        role_id = cast(
            Optional[int],
            await self.bot.db.fetchval(
                query,
                guild.id,
                payload.message_id,
                str(payload.emoji),
            ),
        )
        if (
            not role_id
            or not guild.me.guild_permissions.manage_roles
            or (role := guild.get_role(role_id)) is None
            or role >= guild.me.top_role
            or role in member.roles
        ):
            return

        with suppress(HTTPException):
            await member.add_roles(role, reason="Reaction role assignment")

    @Cog.listener("on_raw_reaction_remove")
    async def reactionrole_removal(self, payload: RawReactionActionEvent) -> None:
        """Remove a role from a member based on their reaction."""

        guild = self.bot.get_guild(payload.guild_id or 0)
        member = payload.member or (guild and guild.get_member(payload.user_id))
        if not guild or not member or member.bot:
            return

        query = """
        SELECT role_id FROM roles.reaction
        WHERE guild_id = $1 AND message_id = $2 AND emoji = $3
        """
        role_id = cast(
            Optional[int],
            await self.bot.db.fetchval(
                query,
                guild.id,
                payload.message_id,
                str(payload.emoji),
            ),
        )
        if (
            not role_id
            or not guild.me.guild_permissions.manage_roles
            or (role := guild.get_role(role_id)) is None
            or role >= guild.me.top_role
            or role not in member.roles
        ):
            return

        with suppress(HTTPException):
            await member.remove_roles(role, reason="Reaction role removal")

    @group(aliases=("reactionroles", "rr"), invoke_without_command=True)
    @has_permissions(manage_roles=True)
    async def reactionrole(self, ctx: Context) -> Message:
        """Allow members to assign roles to themselves."""

        return await ctx.send_help(ctx.command)

    @reactionrole.command(name="add", aliases=("create", "new"))
    @has_permissions(manage_roles=True)
    async def reactionrole_add(
        self,
        ctx: Context,
        message: Message,
        emoji: str,
        *,
        role: Annotated[
            Role,
            StrictRole(check_dangerous=True),
        ],
    ) -> Message:
        """Add a reaction role to a message."""

        if message.guild != ctx.guild:
            return await ctx.warn(
                "You can only add reaction roles to messages in this server"
            )

        try:
            await message.add_reaction(emoji)
        except (HTTPException, TypeError):
            return await ctx.warn("I couldn't add that reaction to the message")

        query = """
        INSERT INTO roles.reaction (
            guild_id,
            channel_id,
            message_id,
            role_id,
            emoji
        ) VALUES ($1, $2, $3, $4, $5)
        """
        try:
            await self.bot.db.execute(
                query,
                ctx.guild.id,
                message.channel.id,
                message.id,
                role.id,
                emoji,
            )
        except UniqueViolationError:
            return await ctx.warn("That reaction role already exists")

        return await ctx.approve(
            f"Now assigning {role.mention} when members react with {emoji} on {message.jump_url}"
        )

    @reactionrole.command(name="remove", aliases=("delete", "del", "rm"))
    @has_permissions(manage_roles=True)
    async def reactionrole_remove(
        self,
        ctx: Context,
        message: Message,
        emoji: str,
    ) -> Message:
        """Remove a reaction role from a message."""

        if message.guild != ctx.guild:
            return await ctx.warn(
                "You can only remove reaction roles from messages in this server"
            )

        query = """DELETE FROM roles.reaction WHERE message_id = $1 AND emoji = $2"""
        result = await self.bot.db.execute(query, message.id, emoji)
        if result == "DELETE 0":
            return await ctx.warn(
                f"No reaction role was found for {emoji} on {message.jump_url}"
            )

        return await ctx.approve(
            f"Removed the reaction role for {emoji} on {message.jump_url}"
        )

    @reactionrole.command(name="clear", aliases=("reset", "purge"))
    @has_permissions(manage_roles=True)
    async def reactionrole_clear(
        self,
        ctx: Context,
        message: Optional[Message],
    ) -> Message:
        """Remove all reaction roles from a message."""

        if not message:
            await ctx.prompt(
                "Are you sure you want to remove all reaction roles in this server"
            )

            query = "DELETE FROM roles.reaction WHERE guild_id = $1"
        elif message.guild != ctx.guild:
            return await ctx.warn(
                "You can only remove reaction roles from messages in this server"
            )
        else:
            query = """DELETE FROM roles.reaction WHERE message_id = $1"""

        result = await self.bot.db.execute(query, ctx.guild.id)
        if result == "DELETE 0":
            return await ctx.warn("No reaction roles have been set up")

        return await ctx.approve(
            f"Removed all reaction roles {'in this server' if not message else 'on that message'}"
        )

    @reactionrole.command(name="list")
    @has_permissions(manage_roles=True)
    async def reactionrole_list(self, ctx: Context) -> Message:
        """View all reaction roles in the server."""

        query = "SELECT * FROM roles.automatic WHERE guild_id = $1"
        records = cast(List[Record], await self.bot.db.fetch(query, ctx.guild.id))
        messages = [
            (
                f"[`{message.id}`]({message.jump_url})"
                f" - {role.mention} for **{record['emoji']}**"
            )
            for record in records
            if (channel := ctx.guild.get_channel(record["channel_id"]))
            and (message := channel.get_partial_message(record["message_id"]))  # type: ignore
            and (role := ctx.guild.get_role(record["role_id"]))
        ]
        if not messages:
            return await ctx.warn("No reaction roles exist in this server")

        embed = Embed(title="Reaction Roles")
        paginator = Paginator(ctx, messages, embed)
        return await paginator.start()
