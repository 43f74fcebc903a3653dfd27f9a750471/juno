from contextlib import suppress
from typing import Annotated, Callable, List, Literal, Optional, cast

from discord import Color, Embed, HTTPException, Member, Message, Role
from discord.ext.commands import (
    BucketType,
    Cog,
    CommandError,
    Greedy,
    MaxConcurrency,
    MaxConcurrencyReached,
    Range,
    group,
    has_permissions,
)
from humanfriendly import format_timespan

from bot.core import Context, Juno
from bot.core.database.settings import Settings
from bot.shared import quietly_delete
from bot.shared.converters.role import StrictRole
from bot.shared.converters.user import HierarchyMember
from bot.shared.formatter import human_join, plural
from bot.shared.paginator import Paginator

MASS_ROLE_CONCURRENCY = MaxConcurrency(1, per=BucketType.guild, wait=False)


async def do_mass_role(
    ctx: Context,
    role: Role,
    predicate: Callable[[Member], bool] = lambda _: True,
    action: Literal["add", "remove"] = "add",
) -> Message:
    """A helper function to do mass role operations."""

    if not ctx.guild.me.guild_permissions.manage_roles:
        raise CommandError("I don't have permission to manage roles")

    if not ctx.guild.chunked:
        await ctx.guild.chunk(cache=True)

    verb = "to" if action == "add" else "from"
    members: List[Member] = []
    for member in ctx.guild.members:
        if not predicate(member):
            continue

        try:
            await HierarchyMember.convert(ctx, str(member.id))
        except CommandError:  # Indicates that the member is not touchable
            continue

        members.append(member)

    if not members:
        raise CommandError(f"No members to {action} {role.mention} {verb}")

    pending_message = await ctx.respond(
        f"{action.title()[:5]}ing {role.mention} {verb} {plural(len(members), md='`'):member}...",
        f"This should take around **{format_timespan(len(members))}**",
    )

    success: List[Member] = []
    failures: List[Member] = []
    async with ctx.typing():
        key = MASS_ROLE_CONCURRENCY.get_key(ctx)
        for member in members:
            if not MASS_ROLE_CONCURRENCY._mapping.get(key):
                break

            method = getattr(member, f"{action}_roles")
            try:
                await method(
                    role,
                    reason=f"Mass {action} by {ctx.author.name} ({ctx.author.id})",
                )
            except HTTPException:
                failures.append(member)
                if len(failures) >= 6:
                    break

            else:
                success.append(member)

    await quietly_delete(pending_message)
    response = [
        f"Successfully {action[:5]}ed {role.mention} {verb} {plural(len(success), md='`'):member}"
    ]
    if failures:
        response.append(
            f"Failed to {action} {role.mention} {verb} {plural(len(failures), md='`'):member}"
        )

    return await ctx.approve(*response)


class ModerationRole(Cog):
    def __init__(self, bot: Juno) -> None:
        self.bot = bot

    async def cog_command_error(
        self,
        ctx: Context,
        error: CommandError,
    ) -> Optional[Message]:
        if isinstance(
            error, MaxConcurrencyReached
        ) and ctx.command.qualified_name.startswith(("role",)):
            return await ctx.warn(
                "There is already an ongoing mass role operation",
                f"You can cancel it early with the `{ctx.clean_prefix}role cancel` command",
            )

        return await super().cog_command_error(ctx, error)

    def role_cache_key(self, member: Member) -> str:
        return f"roles:{member.guild.id}-{member.id}"

    @Cog.listener("on_member_remove")
    async def role_cache(self, member: Member) -> None:
        if member.bot or not member.guild.me.guild_permissions.manage_roles:
            return

        role_ids = [role.id for role in member.roles if role.is_assignable()]
        if not role_ids:
            return

        key = self.role_cache_key(member)
        await self.bot.redis.set(key, role_ids, ex=3600)

    @Cog.listener("on_member_join")
    async def role_restore_cache(self, member: Member) -> None:
        guild = member.guild
        if member.bot or not guild.me.guild_permissions.manage_roles:
            return

        key = self.role_cache_key(member)
        role_ids = cast(Optional[List[int]], await self.bot.redis.get(key))
        if not role_ids:
            return

        roles = [
            role
            for role_id in role_ids
            if (role := member.guild.get_role(role_id)) is not None
            and role.is_assignable()
            and role not in member.roles
        ]
        if not roles:
            return

        settings = await Settings.fetch(self.bot, guild)
        if not settings.reassign_roles:
            return

        roles = filter(lambda role: role not in settings.reassign_ignored_roles, roles)

        await self.bot.redis.delete(key)
        with suppress(HTTPException):
            await member.add_roles(*roles, reason="Reassigned from cache")

    @group(aliases=("r",), invoke_without_command=True)
    @has_permissions(manage_roles=True)
    async def role(
        self,
        ctx: Context,
        members: Greedy[HierarchyMember],
        *,
        role: Annotated[Role, StrictRole],
    ) -> None:
        """Add or remove a role from a member."""

        for member in members:
            if role in member.roles:
                await ctx.invoke(self.role_remove, member=member, role=role)
            else:
                await ctx.invoke(self.role_add, member=member, role=role)

    @role.command(name="add", aliases=("grant",))
    @has_permissions(manage_roles=True)
    async def role_add(
        self,
        ctx: Context,
        member: HierarchyMember,
        *,
        role: Annotated[Role, StrictRole],
    ) -> Message:
        """Add a role to a member."""

        if role in member.roles:
            return await ctx.warn(f"{member.mention} already has {role.mention}")

        reason = f"Added by {ctx.author.name} ({ctx.author.id})"
        await member.add_roles(role, reason=reason)
        return await ctx.approve(f"Added {role.mention} to {member.mention}")

    @role.command(name="remove", aliases=("revoke", "rm"))
    @has_permissions(manage_roles=True)
    async def role_remove(
        self,
        ctx: Context,
        member: HierarchyMember,
        *,
        role: Annotated[Role, StrictRole],
    ) -> Message:
        """Remove a role from a member."""

        if role not in member.roles:
            return await ctx.warn(f"{member.mention} doesn't have {role.mention}")

        reason = f"Removed by {ctx.author.name} ({ctx.author.id})"
        await member.remove_roles(role, reason=reason)
        return await ctx.approve(f"Removed {role.mention} from {member.mention}")

    @role.group(
        name="restore",
        aliases=("reassign", "cache"),
        invoke_without_command=True,
    )
    @has_permissions(manage_roles=True)
    async def role_restore(
        self,
        ctx: Context,
        member: HierarchyMember,
    ) -> Message:
        """Restore a member's roles from cache."""

        key = self.role_cache_key(member)
        role_ids = cast(Optional[List[int]], await self.bot.redis.get(key)) or []
        roles: List[Role] = []
        for role_id in role_ids:
            role = member.guild.get_role(role_id)
            if not role or role in member.roles:
                continue

            try:
                await StrictRole(check_dangerous=True).convert(ctx, str(role.id))
            except CommandError:
                continue

            roles.append(role)

        if not roles:
            return await ctx.warn(f"No roles to restore for {member.mention}")

        human_roles = human_join(
            [role.mention for role in roles],
            final="and",
        )
        await member.add_roles(
            *roles,
            reason=f"Restored from cache by {ctx.author.name} ({ctx.author.id})",
        )
        return await ctx.approve(f"Restored {human_roles} to {member.mention}")

    @role_restore.group(
        name="reassign",
        aliases=(
            "rejoin",
            "auto",
        ),
        invoke_without_command=True,
    )
    @has_permissions(manage_roles=True)
    async def role_restore_reassign(self, ctx: Context) -> Message:
        """Toggle auto reassigning roles when members rejoin."""

        await ctx.settings.upsert(reassign_roles=not ctx.settings.reassign_roles)
        return await ctx.approve(
            f"{'Now' if ctx.settings.reassign_roles else 'No longer'} reassigning roles when members rejoin"
        )

    @role_restore_reassign.group(
        name="exclude",
        aliases=("exempt", "ignore"),
        invoke_without_command=True,
    )
    @has_permissions(manage_roles=True)
    async def role_restore_reassign_ignore(
        self,
        ctx: Context,
        *,
        role: Annotated[Role, StrictRole],
    ) -> Message:
        """Exclude a role from being re-assigned."""

        if role in ctx.settings.reassign_ignored_roles:
            return await ctx.warn(
                f"Already excluding {role.mention} from being re-assigned"
            )

        ctx.settings.record["reassign_ignored_roles"].append(role.id)
        await ctx.settings.upsert()
        return await ctx.approve(f"Now excluding {role.mention} when reassigning roles")

    @role_restore_reassign_ignore.command(name="remove", aliases=("rm",))
    @has_permissions(manage_roles=True)
    async def role_restore_reassign_ignore_remove(
        self,
        ctx: Context,
        *,
        role: Annotated[Role, StrictRole],
    ) -> Message:
        """Allow a role to be re-assigned."""

        if role not in ctx.settings.reassign_ignored_roles:
            return await ctx.warn(f"Already allowing {role.mention} to be re-assigned")

        ctx.settings.record["reassign_ignored_roles"].remove(role.id)
        await ctx.settings.upsert()
        return await ctx.approve(f"Now allowing {role.mention} to be re-assigned")

    @role_restore_reassign_ignore.command(name="clear", aliases=("reset",))
    @has_permissions(manage_roles=True)
    async def role_restore_reassign_ignore_clear(self, ctx: Context) -> Message:
        """Remove all roles that are being excluded."""

        ctx.settings.record["reassign_ignored_roles"].clear()
        await ctx.settings.upsert()
        return await ctx.approve("Now allowing all roles to be re-assigned")

    @role_restore_reassign_ignore.command(name="list")
    @has_permissions(manage_roles=True)
    async def role_restore_reassign_ignore_list(self, ctx: Context) -> Message:
        """View all roles that are being excluded."""

        roles = ctx.settings.reassign_ignored_roles
        if not roles:
            return await ctx.warn("No roles are excluded from being re-assigned")

        embed = Embed(title="Excluded Roles")
        entries = [f"{role.mention} (`{role.id}`)" for role in roles]
        paginator = Paginator(ctx, entries, embed)
        return await paginator.start()

    @role.command(name="create", aliases=("make",))
    @has_permissions(manage_roles=True)
    async def role_create(
        self,
        ctx: Context,
        color: Optional[Color] = None,
        hoist: Optional[bool] = None,
        *,
        name: Range[str, 1, 100],
    ) -> Message:
        """Create a role."""

        if len(ctx.guild.roles) >= 250:
            return await ctx.warn("This server has reached the maximum amount of roles")

        reason = f"Created by {ctx.author.name} ({ctx.author.id})"
        role = await ctx.guild.create_role(
            name=name,
            color=color or Color.default(),
            hoist=hoist or False,
            reason=reason,
        )
        return await ctx.approve(f"Created the {role.mention} role")

    @role.command(name="duplicate", aliases=("copy",))
    @has_permissions(manage_roles=True)
    async def role_duplicate(
        self,
        ctx: Context,
        role: Annotated[
            Role,
            StrictRole(check_integrated=False),
        ],
        color: Optional[Color] = None,
        hoist: Optional[bool] = None,
        *,
        name: Range[str, 1, 100],
    ) -> Message:
        """Duplicate a role with a new name."""

        if len(ctx.guild.roles) >= 250:
            return await ctx.warn("This server has reached the maximum amount of roles")

        reason = f"Duplicated by {ctx.author.name} ({ctx.author.id})"
        new_role = await ctx.guild.create_role(
            name=name,
            color=color or role.color,
            hoist=hoist or role.hoist,
            permissions=role.permissions,
            reason=reason,
        )

        return await ctx.approve(f"Duplicated {role.mention} as {new_role.mention}")

    @role.command(name="delete", aliases=("del",))
    @has_permissions(manage_roles=True)
    async def role_delete(
        self,
        ctx: Context,
        *,
        role: Annotated[Role, StrictRole],
    ) -> Optional[Message]:
        """Delete a role."""

        if role.members:
            await ctx.prompt(
                f"{role.mention} has {plural(len(role.members), md='`'):member}, are you sure you want to delete it?",
            )

        await role.delete()
        return await ctx.add_check()

    @role.command(name="color", aliases=("colour",))
    @has_permissions(manage_roles=True)
    async def role_color(
        self,
        ctx: Context,
        role: Annotated[
            Role,
            StrictRole(check_integrated=False),
        ],
        *,
        color: Color,
    ) -> Message:
        """Change a role's color."""

        reason = f"Changed by {ctx.author.name} ({ctx.author.id})"
        await role.edit(color=color, reason=reason)
        return await ctx.approve(f"Changed {role.mention}'s color to `{color}`")

    @role.command(name="rename", aliases=("name",))
    @has_permissions(manage_roles=True)
    async def role_rename(
        self,
        ctx: Context,
        role: Annotated[
            Role,
            StrictRole(check_integrated=False),
        ],
        *,
        name: Range[str, 1, 100],
    ) -> None:
        """Change a role's name."""

        reason = f"Changed by {ctx.author.name} ({ctx.author.id})"
        await role.edit(name=name, reason=reason)
        return await ctx.add_check()

    @role.command(name="hoist")
    @has_permissions(manage_roles=True)
    async def role_hoist(
        self,
        ctx: Context,
        *,
        role: Annotated[
            Role,
            StrictRole(check_integrated=False),
        ],
    ) -> Message:
        """Toggle if a role should appear in the sidebar."""

        reason = f"Changed by {ctx.author.name} ({ctx.author.id})"
        await role.edit(hoist=not role.hoist, reason=reason)
        return await ctx.approve(
            f"{role.mention} is {'now' if role.hoist else 'no longer'} hoisted"
        )

    @role.command(name="mentionable")
    @has_permissions(manage_roles=True)
    async def role_mentionable(
        self,
        ctx: Context,
        *,
        role: Annotated[
            Role,
            StrictRole(check_integrated=False),
        ],
    ) -> Message:
        """Toggle if a role should be mentionable."""

        reason = f"Changed by {ctx.author.name} ({ctx.author.id})"
        await role.edit(mentionable=not role.mentionable, reason=reason)
        return await ctx.approve(
            f"{role.mention} is {'now' if role.mentionable else 'no longer'} mentionable"
        )

    @role.command(name="cancel")
    @has_permissions(manage_roles=True)
    async def role_cancel(self, ctx: Context) -> Optional[Message]:
        """Cancel an ongoing mass role operation."""

        key = MASS_ROLE_CONCURRENCY.get_key(ctx)
        if key not in MASS_ROLE_CONCURRENCY._mapping:
            return await ctx.warn(
                "There isn't an ongoing mass role operation to cancel"
            )

        await MASS_ROLE_CONCURRENCY.release(ctx)
        return await ctx.add_check()

    @role.group(
        name="all",
        aliases=("everyone",),
        invoke_without_command=True,
        max_concurrency=MASS_ROLE_CONCURRENCY,
    )
    @has_permissions(manage_roles=True)
    async def role_all(
        self,
        ctx: Context,
        *,
        role: Annotated[
            Role,
            StrictRole(check_dangerous=True),
        ],
    ) -> Message:
        """Add a role to all members."""

        return await do_mass_role(ctx, role)

    @role_all.command(
        name="remove",
        aliases=("revoke", "rm"),
        max_concurrency=MASS_ROLE_CONCURRENCY,
    )
    @has_permissions(manage_roles=True)
    async def role_all_remove(
        self,
        ctx: Context,
        *,
        role: Annotated[Role, StrictRole],
    ) -> Message:
        """Remove a role from all members."""

        return await do_mass_role(
            ctx,
            role,
            lambda member: role in member.roles,
            action="remove",
        )

    @role.group(
        name="humans",
        invoke_without_command=True,
        max_concurrency=MASS_ROLE_CONCURRENCY,
    )
    @has_permissions(manage_roles=True)
    async def role_humans(
        self,
        ctx: Context,
        *,
        role: Annotated[
            Role,
            StrictRole(check_dangerous=True),
        ],
    ) -> Message:
        """Add a role to all human members."""

        return await do_mass_role(
            ctx,
            role,
            lambda member: not member.bot,
        )

    @role_humans.command(
        name="remove",
        aliases=("revoke", "rm"),
        max_concurrency=MASS_ROLE_CONCURRENCY,
    )
    @has_permissions(manage_roles=True)
    async def role_humans_remove(
        self,
        ctx: Context,
        *,
        role: Annotated[Role, StrictRole],
    ) -> Message:
        """Remove a role from all human members."""

        return await do_mass_role(
            ctx,
            role,
            lambda member: not member.bot and role in member.roles,
            action="remove",
        )

    @role.group(
        name="bots",
        invoke_without_command=True,
        max_concurrency=MASS_ROLE_CONCURRENCY,
    )
    @has_permissions(manage_roles=True)
    async def role_bots(
        self,
        ctx: Context,
        *,
        role: Annotated[
            Role,
            StrictRole(check_dangerous=True),
        ],
    ) -> Message:
        """Add a role to all bots."""

        return await do_mass_role(
            ctx,
            role,
            lambda member: member.bot,
        )

    @role_bots.command(
        name="remove",
        aliases=("revoke", "rm"),
        max_concurrency=MASS_ROLE_CONCURRENCY,
    )
    @has_permissions(manage_roles=True)
    async def role_bots_remove(
        self,
        ctx: Context,
        *,
        role: Annotated[Role, StrictRole],
    ) -> Message:
        """Remove a role from all bots."""

        return await do_mass_role(
            ctx,
            role,
            lambda member: member.bot and role in member.roles,
            action="remove",
        )

    @role.group(
        name="has",
        aliases=("with", "in"),
        invoke_without_command=True,
        max_concurrency=MASS_ROLE_CONCURRENCY,
    )
    @has_permissions(manage_roles=True)
    async def role_has(
        self,
        ctx: Context,
        role: Annotated[
            Role,
            StrictRole(
                check_integrated=False,
            ),
        ],
        *,
        assign_role: Annotated[
            Role,
            StrictRole(check_dangerous=True),
        ],
    ) -> Message:
        """Add a role to all members with another role."""

        return await do_mass_role(
            ctx,
            assign_role,
            lambda member: role in member.roles,
        )

    @role_has.command(
        name="remove",
        aliases=("revoke", "rm"),
        max_concurrency=MASS_ROLE_CONCURRENCY,
    )
    @has_permissions(manage_roles=True)
    async def role_has_remove(
        self,
        ctx: Context,
        role: Annotated[Role, StrictRole],
        *,
        assign_role: Annotated[Role, StrictRole],
    ) -> Message:
        """Remove a role from all members with another role."""

        return await do_mass_role(
            ctx,
            assign_role,
            lambda member: role in member.roles,
            action="remove",
        )
