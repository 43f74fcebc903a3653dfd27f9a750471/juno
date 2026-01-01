import asyncio
from logging import getLogger
from random import uniform
from typing import Annotated, List, Literal, TypedDict, cast

from discord import Embed, Guild, HTTPException, Member, Message, Role
from discord.ext.commands import Cog, Range, flag, group, has_permissions
from humanfriendly import format_timespan

from bot.core import Context, Juno
from bot.shared.converters import FlagConverter
from bot.shared.converters.role import StrictRole
from bot.shared.paginator import Paginator

logger = getLogger("bot.roles")


class Record(TypedDict):
    guild_id: int
    role_id: int
    action: Literal["add", "remove"]
    delay: float


class Flags(FlagConverter):
    delay: Range[int, 1, 160] = flag(
        default=0,
        description="The delay in seconds before the role is assigned or removed.",
    )
    action: Literal["add", "remove"] = flag(
        default="add",
        description="Whether to add or remove the role.",
    )


async def assign_role(
    guild: Guild,
    member_id: int,
    role: Role,
    action: Literal["add", "remove"],
    delay: float,
) -> None:
    """Assign or remove a role from a member."""

    delay = delay or uniform(0.5, 1.5)
    await asyncio.sleep(delay)

    member = guild.get_member(member_id)
    if (
        not member
        or not guild.me.guild_permissions.manage_roles
        or role >= guild.me.top_role
    ):
        return

    reason = f"Automatic role assignment after {delay:.2f} seconds"
    try:
        if action == "add" and role not in member.roles:
            await member.add_roles(role, reason=reason)

        elif action == "remove" and role in member.roles:
            await member.remove_roles(role, reason=reason)
    except HTTPException as exc:
        method = "to" if action == "add" else "from"
        logger.error(
            f"Failed to {action} role {role} {method} {member} in {guild} ({guild.id})",
            exc_info=exc,
        )


class AutomaticRoles(Cog):
    def __init__(self, bot: Juno) -> None:
        self.bot = bot

    @Cog.listener("on_member_join")
    async def autorole_assign(self, member: Member) -> None:
        """Assign roles to a member when they join the server."""

        query = "SELECT * FROM roles.automatic WHERE guild_id = $1"
        records = cast(List[Record], await self.bot.db.fetch(query, member.guild.id))

        scheduled_deletion: List[int] = []
        for record in records:
            role = member.guild.get_role(record["role_id"])
            if not role:
                scheduled_deletion.append(record["role_id"])
                continue

            asyncio.create_task(
                assign_role(
                    member.guild,
                    member.id,
                    role,
                    record["action"],
                    record["delay"],
                )
            )

        if scheduled_deletion:
            query = "DELETE FROM roles.automatic WHERE role_id = ANY($1::BIGINT[])"
            await self.bot.db.execute(query, scheduled_deletion)

    @group(invoke_without_command=True)
    @has_permissions(manage_roles=True)
    async def autorole(self, ctx: Context) -> Message:
        """Automatically assign roles to new members."""

        return await ctx.send_help(ctx.command)

    @autorole.command(name="add", aliases=("create", "new"))
    @has_permissions(manage_roles=True)
    async def autorole_add(
        self,
        ctx: Context,
        role: Annotated[
            Role,
            StrictRole(check_dangerous=True),
        ],
        *,
        flags: Flags,
    ) -> Message:
        """Add a role to be assigned automatically."""

        if flags.action == "remove" and not flags.delay:
            return await ctx.warn("You must provide a delay for removing roles")

        await self.bot.db.execute(
            """
            INSERT INTO roles.automatic (
                guild_id,
                role_id,
                action,
                delay
            ) VALUES ($1, $2, $3, $4)
            ON CONFLICT (guild_id, role_id, action)
            DO UPDATE SET
                delay = EXCLUDED.delay
            """,
            ctx.guild.id,
            role.id,
            flags.action,
            flags.delay,
        )

        return await ctx.approve(
            (
                f"Now assigning {role.mention} to new members"
                if flags.action == "add"
                else f"Now removing {role.mention} from members"
            )
            + (f" with a `{format_timespan(flags.delay)}` delay" if flags.delay else "")
        )

    @autorole.command(name="remove", aliases=("delete", "del", "rm"))
    @has_permissions(manage_roles=True)
    async def autorole_remove(
        self,
        ctx: Context,
        *,
        role: Annotated[Role, StrictRole],
    ) -> Message:
        """Remove a role from automatic assignment."""

        query = "DELETE FROM roles.automatic WHERE guild_id = $1 AND role_id = $2"
        result = await self.bot.db.execute(query, ctx.guild.id, role.id)
        if result == "DELETE 0":
            return await ctx.warn(f"{role.mention} is not being assigned automatically")

        return await ctx.approve(f"No longer assigning {role.mention} to new members")

    @autorole.command(name="clear", aliases=("reset", "purge"))
    @has_permissions(manage_roles=True)
    async def autorole_clear(self, ctx: Context) -> Message:
        """Remove all roles from automatic assignment."""

        query = "DELETE FROM roles.automatic WHERE guild_id = $1"
        result = await self.bot.db.execute(query, ctx.guild.id)
        if result == "DELETE 0":
            return await ctx.warn("No roles are being assigned automatically")

        return await ctx.approve("No longer assigning roles automatically")

    @autorole.command(name="list")
    @has_permissions(manage_roles=True)
    async def autorole_list(self, ctx: Context) -> Message:
        """View all roles being assigned automatically."""

        query = "SELECT * FROM roles.automatic WHERE guild_id = $1"
        records = cast(List[Record], await self.bot.db.fetch(query, ctx.guild.id))
        roles = [
            f"`{str(index).zfill(2)}{record['action'][0].upper()}` {role.mention}"
            + (
                f" after `{format_timespan(record['delay'])}`"
                if record["delay"]
                else ""
            )
            for index, record in enumerate(records, start=1)
            if (role := ctx.guild.get_role(record["role_id"]))
        ]
        if not roles:
            return await ctx.warn("No roles are being assigned automatically")

        embed = Embed(title="Automatic Roles")
        paginator = Paginator(ctx, roles, embed, counter=False)
        return await paginator.start()
