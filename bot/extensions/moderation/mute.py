import asyncio
from contextlib import suppress
from datetime import timedelta
from typing import Annotated, Optional

from discord import (
    Embed,
    HTTPException,
    Member,
    Message,
    PermissionOverwrite,
    Permissions,
    Role,
    TextChannel,
)
from discord.ext.commands import (
    BucketType,
    Cog,
    command,
    cooldown,
    group,
    has_permissions,
    max_concurrency,
    parameter,
)
from discord.utils import format_dt, get
from humanfriendly import format_timespan

from bot.core import Context, Juno
from bot.shared.converters.time import Duration
from bot.shared.converters.user import HierarchyMember
from bot.shared.formatter import plural
from bot.shared.managers.failure import FailureManager
from bot.shared.paginator import Paginator
from bot.shared.timer import Timer

from .history.case import Action, Case

DEFAULT_REASON = "No reason provided"


async def configure_settings(ctx: Context, sync: bool = False) -> None:
    async def create_or_sync_role(name: str, permissions: Permissions) -> Role:
        role = get(ctx.guild.roles, name=name)
        if not role:
            role = await ctx.guild.create_role(name=name, permissions=permissions)

        elif sync:
            await role.edit(permissions=permissions)

        return role

    async def create_or_sync_jail_channel(jail_role: Role) -> TextChannel:
        jail_channel = ctx.settings.jail_channel or get(
            ctx.guild.text_channels,
            name="jail",
        )
        overwrites = {
            jail_role: PermissionOverwrite(
                read_messages=True,
                send_messages=True,
                read_message_history=True,
            ),
            ctx.guild.default_role: PermissionOverwrite(
                read_messages=False,
                send_messages=False,
            ),
        }

        if not jail_channel:
            jail_channel = await ctx.guild.create_text_channel(
                name="jail",
                overwrites=overwrites,  # type: ignore
            )

        elif sync:
            await jail_channel.edit(overwrites=overwrites)

        return jail_channel

    command = ctx.command.qualified_name
    if command.startswith("mute"):
        mute_role = await create_or_sync_role("muted", Permissions(send_messages=False))
        if not ctx.settings.mute_role or sync:
            await asyncio.gather(
                *[
                    channel.set_permissions(mute_role, send_messages=False)
                    for channel in ctx.guild.text_channels
                ]
            )

        if not ctx.settings.mute_role:
            await ctx.send(
                "The mute role has been created and configured..",
                delete_after=6,
            )
            await ctx.settings.upsert(mute_role_id=mute_role.id)

    if command.startswith("jail"):
        jail_role = await create_or_sync_role(
            "jailed",
            Permissions(send_messages=False, speak=False),
        )
        jail_channel = await create_or_sync_jail_channel(jail_role)

        if not ctx.settings.jail_role or not ctx.settings.jail_channel or sync:
            await asyncio.gather(
                *[
                    channel.set_permissions(
                        jail_role,
                        read_messages=False,
                        send_messages=False,
                        read_message_history=False,
                    )
                    for channel in ctx.guild.text_channels
                    if channel != jail_channel
                ]
            )

        if not ctx.settings.jail_role or not ctx.settings.jail_channel:
            await ctx.send(
                "The jail role and channel have been created and configured..",
                delete_after=6,
            )
            await ctx.settings.upsert(
                jail_role_id=jail_role.id, jail_channel_id=jail_channel.id
            )


class Mute(Cog):
    def __init__(self, bot: Juno) -> None:
        self.bot = bot

    async def cog_before_invoke(self, ctx: Context) -> None:
        command = ctx.command.qualified_name
        if command.startswith(("mute", "jail")) and not command.endswith("sync"):
            await configure_settings(ctx)

        return await super().cog_before_invoke(ctx)

    @Cog.listener()
    async def on_temporary_mute_timer_complete(self, timer: Timer) -> None:
        guild_id = int(timer.kwargs["guild_id"])
        user_id = int(timer.kwargs["user_id"])
        role_id = int(timer.kwargs["role_id"])
        reason = timer.kwargs["reason"]

        guild = self.bot.get_guild(guild_id)
        if not guild:
            return

        member = guild.get_member(user_id)
        role = guild.get_role(role_id)
        if not member or not role:
            return

        elif role not in member.roles:
            return

        with suppress(HTTPException):
            await member.remove_roles(role, reason=reason)
            await Case.create(guild, member, Action.UNMUTE, f"Temporary / {reason}")

    @group(aliases=("shutup",), invoke_without_command=True)
    @has_permissions(moderate_members=True)
    async def mute(
        self,
        ctx: Context,
        member: Annotated[Member, HierarchyMember],
        duration: Optional[Annotated[timedelta, Duration]] = None,
        *,
        reason: str = DEFAULT_REASON,
    ) -> Optional[Message]:
        """Restrict a member from sending messages.

        It's recommended to enable `role restore reassign`
        this will restore the member's mute role if they leave."""

        assert ctx.settings.mute_role
        if ctx.settings.mute_role in member.roles:
            return await ctx.warn(f"{member.mention} is already muted")

        elif duration and duration.total_seconds() < 60:
            return await ctx.warn("The duration provided is too short, minimum is `1m`")

        await member.add_roles(
            ctx.settings.mute_role,
            reason=f"{reason} {ctx.author} ({ctx.author.id})",
        )
        await Case.create(
            ctx,
            member,
            Action.MUTE,
            reason,
            action_expiration=(ctx.message.created_at + duration) if duration else None,
        )
        if duration:
            await Timer.create(
                self.bot,
                "temporary_mute",
                ctx.message.created_at + duration,
                guild_id=ctx.guild.id,
                user_id=member.id,
                role_id=ctx.settings.mute_role.id,
                reason=f"{reason} {ctx.author} ({ctx.author.id})",
            )
            return await ctx.reply(
                f"{member} has been muted for {format_timespan(duration, max_units=2)}",
            )

        return await ctx.add_check()

    @mute.command(name="sync", aliases=("permissions", "perms"))
    @has_permissions(manage_channels=True, manage_roles=True)
    @cooldown(1, 30, BucketType.guild)
    async def mute_sync(self, ctx: Context) -> None:
        """Sync the mute role permissions with all channels."""

        async with ctx.typing():
            await configure_settings(ctx, sync=True)
            return await ctx.add_check()

    @mute.command(name="list", aliases=("members", "users"))
    @has_permissions(moderate_members=True)
    async def mute_list(self, ctx: Context) -> Message:
        """View all members that are muted."""

        assert ctx.settings.mute_role
        members = [
            f"{member} [`{member.id}`]"
            for member in ctx.guild.members
            if ctx.settings.mute_role in member.roles
        ]
        if not members:
            return await ctx.warn("No members are currently jailed")

        embed = Embed(title="Muted Members")
        paginator = Paginator(ctx, members, embed)
        return await paginator.start()

    @command(aliases=("speak",))
    @has_permissions(moderate_members=True)
    async def unmute(
        self,
        ctx: Context,
        member: Annotated[Member, HierarchyMember],
        *,
        reason: str = DEFAULT_REASON,
    ) -> Optional[Message]:
        """Allow a member to send messages."""

        assert ctx.settings.mute_role
        if ctx.settings.mute_role not in member.roles:
            return await ctx.warn(f"{member.mention} is not muted")

        await member.remove_roles(
            ctx.settings.mute_role,
            reason=f"{reason} {ctx.author} ({ctx.author.id})",
        )
        await Case.create(ctx, member, Action.UNMUTE, reason)
        return await ctx.add_check()

    @group(aliases=("prison",), invoke_without_command=True)
    @has_permissions(manage_roles=True)
    async def jail(
        self,
        ctx: Context,
        member: Annotated[Member, HierarchyMember],
        duration: Optional[Annotated[timedelta, Duration]] = None,
        *,
        reason: str = DEFAULT_REASON,
    ) -> Optional[Message]:
        """Restrict a member from viewing channels.

        It's recommended to enable `role restore reassign`
        this will restore the member's jail role if they leave."""

        assert ctx.settings.jail_role
        if ctx.settings.jail_role in member.roles:
            return await ctx.warn(f"{member.mention} is already jailed")

        elif duration and duration.total_seconds() < 60:
            return await ctx.warn("The duration provided is too short, minimum is `1m`")

        await member.add_roles(
            ctx.settings.jail_role,
            reason=f"{reason} {ctx.author} ({ctx.author.id})",
        )
        await Case.create(
            ctx,
            member,
            Action.JAIL,
            reason,
            action_expiration=(ctx.message.created_at + duration) if duration else None,
        )
        if duration:
            await Timer.create(
                self.bot,
                "temporary_mute",
                ctx.message.created_at + duration,
                guild_id=ctx.guild.id,
                user_id=member.id,
                role_id=ctx.settings.jail_role.id,
                reason=f"{reason} {ctx.author} ({ctx.author.id})",
            )
            return await ctx.reply(
                f"{member} has been jailed for {format_timespan(duration, max_units=2)}",
            )

        return await ctx.add_check()

    @jail.command(name="sync", aliases=("permissions", "perms"))
    @has_permissions(manage_channels=True, manage_roles=True)
    @cooldown(1, 30, BucketType.guild)
    async def jail_sync(self, ctx: Context) -> None:
        """Sync the jail role permissions with all channels."""

        async with ctx.typing():
            await configure_settings(ctx, sync=True)
            return await ctx.add_check()

    @jail.command(name="list", aliases=("members", "users"))
    @has_permissions(manage_roles=True)
    async def jail_list(self, ctx: Context) -> Message:
        """View all members that are jailed."""

        assert ctx.settings.jail_role
        members = [
            f"{member} [`{member.id}`]"
            for member in ctx.guild.members
            if ctx.settings.jail_role in member.roles
        ]
        if not members:
            return await ctx.warn("No members are currently jailed")

        embed = Embed(title="Jailed Members")
        paginator = Paginator(ctx, members, embed)
        return await paginator.start()

    @command(aliases=("release",))
    @has_permissions(manage_roles=True)
    async def unjail(
        self,
        ctx: Context,
        member: Annotated[Member, HierarchyMember],
        *,
        reason: str = DEFAULT_REASON,
    ) -> Optional[Message]:
        """Allow a member to view channels."""

        assert ctx.settings.jail_role
        if ctx.settings.jail_role not in member.roles:
            return await ctx.warn(f"{member.mention} is not jailed")

        await member.remove_roles(
            ctx.settings.jail_role,
            reason=f"{reason} {ctx.author} ({ctx.author.id})",
        )
        await Case.create(ctx, member, Action.UNJAIL, reason)
        return await ctx.add_check()

    @group(aliases=("tmo", "to"), invoke_without_command=True)
    @has_permissions(moderate_members=True)
    async def timeout(
        self,
        ctx: Context,
        member: Annotated[Member, HierarchyMember],
        duration: timedelta = parameter(
            converter=Duration(
                min=timedelta(minutes=1),
                max=timedelta(days=27),
            ),
        ),
        *,
        reason: str = DEFAULT_REASON,
    ) -> Optional[Message]:
        """Timeout a member from sending messages."""

        if member.guild_permissions.manage_messages:
            return await ctx.warn(
                f"{member.mention} is a moderator and cannot be timed out"
            )

        await member.timeout(
            duration,
            reason=f"{reason} {ctx.author} ({ctx.author.id})",
        )
        await Case.create(
            ctx,
            member,
            Action.TIMEOUT,
            reason,
            action_expiration=member.timed_out_until,
        )
        return await ctx.add_check()

    @timeout.command(name="list", aliases=("members", "users"))
    @has_permissions(moderate_members=True)
    async def timeout_list(self, ctx: Context) -> Message:
        """View all members that are timed out."""

        members = [
            f"{member} expires {format_dt(member.timed_out_until, 'R')}"
            for member in ctx.guild.members
            if member.is_timed_out() and member.timed_out_until
        ]
        if not members:
            return await ctx.warn("No members are currently timed out")

        embed = Embed(title="Timed Out Members")
        paginator = Paginator(ctx, members, embed)
        return await paginator.start()

    @group(aliases=("untmo", "unto", "utmo", "uto"), invoke_without_command=True)
    @has_permissions(moderate_members=True)
    async def untimeout(
        self,
        ctx: Context,
        member: Annotated[Member, HierarchyMember],
        *,
        reason: str = DEFAULT_REASON,
    ) -> Optional[Message]:
        """Lift a member's timeout."""

        if not member.is_timed_out():
            return await ctx.warn(f"{member.mention} is not timed out")

        await member.timeout(None, reason=f"{reason} {ctx.author} ({ctx.author.id})")
        await Case.create(ctx, member, Action.UNTIMEOUT, reason)
        return await ctx.add_check()

    @untimeout.command(name="all", aliases=("everyone",))
    @has_permissions(administrator=True)
    @max_concurrency(1, BucketType.guild)
    async def untimeout_all(
        self,
        ctx: Context,
        *,
        reason: str = DEFAULT_REASON,
    ) -> Message:
        """Lift all timeouts."""

        members = [member for member in ctx.guild.members if member.is_timed_out()]
        if not members:
            return await ctx.warn("No members are currently timed out")

        await ctx.prompt(
            f"Are you sure you want to untimeout {plural(members, '`'):member}?"
        )
        async with ctx.typing(), FailureManager(max_failures=5) as manager:
            for member in members:
                await manager.attempt(
                    member.timeout(
                        None,
                        reason=f"{reason} {ctx.author} ({ctx.author.id})",
                    )
                )

        await Case.create(ctx, ctx.guild, Action.UNTIMEOUT_ALL, reason)
        return await ctx.approve(
            f"Successfully untimeouted {plural(manager.successes, '`'):member}"
        )
