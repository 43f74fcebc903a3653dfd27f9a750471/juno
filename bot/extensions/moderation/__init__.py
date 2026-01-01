from __future__ import annotations

from contextlib import suppress
from datetime import timedelta
from typing import Annotated, Literal, Optional, cast

from discord import (
    AuditLogEntry,
    Embed,
    HTTPException,
    Member,
    Message,
    NotFound,
    Role,
    StageChannel,
    TextChannel,
    Thread,
    User,
    VoiceChannel,
)
from discord.ext.commands import (
    BucketType,
    Cog,
    Greedy,
    Range,
    command,
    cooldown,
    flag,
    group,
    has_permissions,
    max_concurrency,
    parameter,
)
from discord.utils import format_dt
from humanize import precisedelta

from bot.core import Context, Juno
from bot.shared.converters import FlagConverter
from bot.shared.converters.role import StrictRole
from bot.shared.converters.time import Duration
from bot.shared.converters.user import HierarchyMember
from bot.shared.formatter import plural
from bot.shared.managers.failure import FailureManager
from bot.shared.paginator import Paginator
from bot.shared.stopwatch import Stopwatch
from bot.shared.timer import Timer

from ..config import Configuration
from .emoji import Emoji
from .history import Action, Case, History
from .mute import Mute
from .nuke import Nuke
from .purge import Purge
from .role import ModerationRole
from .undo import Undo

DEFAULT_REASON = "No reason provided"


def forcenick_key(guild_id: int, user_id: int) -> str:
    return f"nickname:{guild_id}.{user_id}"


class BanFlags(FlagConverter):
    schedule: Optional[timedelta] = flag(
        default=None,
        converter=Duration(
            min=timedelta(minutes=1),
            max=timedelta(days=31),
        ),
        aliases=["at", "in", "after"],
        description="Execute the ban after a delay",
    )


class Moderation(ModerationRole, History, Nuke, Emoji, Purge, Mute, Undo, Cog):
    def __init__(self, bot: Juno) -> None:
        self.bot = bot

    @Cog.listener()
    async def on_scheduled_ban_timer_complete(self, timer: Timer) -> None:
        guild_id = int(timer.kwargs["guild_id"])
        user_id = int(timer.kwargs["user_id"])
        history = int(timer.kwargs["history"])
        reason = timer.kwargs["reason"]

        guild = self.bot.get_guild(guild_id)
        if not guild:
            return

        member = guild.get_member(user_id)
        if not member:
            return

        await guild.ban(
            member,
            delete_message_days=history,
            reason=reason,
        )

    @Cog.listener("on_member_update")
    async def nickname_force_listener(self, before: Member, after: Member) -> None:
        if before.nick == after.nick:
            return

        key = forcenick_key(before.guild.id, before.id)
        nickname = cast(Optional[str], await self.bot.redis.get(key))
        if not nickname or nickname == after.display_name:
            return

        with suppress(HTTPException):
            await after.edit(nick=nickname, reason="Nickname being forced")

    @Cog.listener("on_member_join")
    async def nickname_force_join_listener(self, member: Member) -> None:
        key = forcenick_key(member.guild.id, member.id)
        nickname = cast(Optional[str], await self.bot.redis.get(key))
        if not nickname or nickname == member.display_name:
            return

        with suppress(HTTPException):
            await member.edit(nick=nickname, reason="Nickname being forced")

    @Cog.listener("on_audit_log_entry_member_update")
    async def nickname_force_audit_listener(self, entry: AuditLogEntry) -> None:
        if not entry.user or not entry.target:
            return

        elif not entry.user.bot or entry.user == self.bot.user:
            return

        if not isinstance(entry.target, Member) or not hasattr(entry.after, "nick"):
            return

        key = forcenick_key(entry.guild.id, entry.target.id)
        nickname = cast(Optional[str], await self.bot.redis.get(key))
        if not nickname or getattr(entry.after, "nick") == nickname:
            return

        key = f"{key}:audit"
        ratelimited = await self.bot.redis.ratelimited(key, limit=5, timespan=15)
        if ratelimited:
            await self.bot.redis.delete(key)

    @command(aliases=("deport",), extras={"flags": BanFlags})
    @has_permissions(ban_members=True)
    async def ban(
        self,
        ctx: Context,
        user: Member | User,
        history: Optional[Range[int, 0, 7]] = None,
        *,
        reason: str = DEFAULT_REASON,
    ) -> Optional[Message]:
        """Ban a member from the server."""

        reason, flags = await BanFlags().find(ctx, reason)
        if not reason:
            reason = DEFAULT_REASON

        if isinstance(user, Member):
            await HierarchyMember.convert(ctx, str(user.id))
            if user.premium_since:
                await ctx.prompt(
                    f"Are you sure you want to ban {user.mention}?",
                    "They are currently boosting the server",
                )

        if flags.schedule:
            timer = await Timer.create(
                self.bot,
                "scheduled_ban",
                ctx.message.created_at + flags.schedule,
                guild_id=ctx.guild.id,
                user_id=user.id,
                history=history or 0,
                reason=f"{reason} {ctx.author} ({ctx.author.id})",
            )
            return await ctx.reply(
                f"{user} will be banned {format_dt(timer.expires_at, 'R')}",
            )

        await ctx.guild.ban(
            user,
            delete_message_days=history or 0,
            reason=f"{reason} {ctx.author} ({ctx.author.id})",
        )
        action = Action.BAN if isinstance(user, Member) else Action.HACKBAN
        await Case.create(ctx, user, action, reason)

        return await ctx.add_check()

    @command()
    @has_permissions(ban_members=True)
    async def softban(
        self,
        ctx: Context,
        member: Annotated[Member, HierarchyMember],
        history: Optional[Range[int, 0, 7]] = None,
        *,
        reason: str = DEFAULT_REASON,
    ) -> None:
        """Ban then unban a member from the server.

        This will delete the member's messages in all channels.
        """

        if member.premium_since:
            await ctx.prompt(
                f"Are you sure you want to softban {member.mention}?",
                "They are currently boosting the server",
            )

        await ctx.guild.ban(
            member,
            delete_message_days=history or 7,
            reason=f"{reason} {ctx.author} ({ctx.author.id})",
        )
        await member.unban(reason=f"{reason} {ctx.author} ({ctx.author.id})")
        await Case.create(ctx, member, Action.SOFTBAN, reason)

        return await ctx.add_check()

    @group(aliases=("pardon", "unb"), invoke_without_command=True)
    @has_permissions(ban_members=True)
    async def unban(
        self,
        ctx: Context,
        user: User,
        *,
        reason: str = DEFAULT_REASON,
    ) -> Optional[Message]:
        """Unban a user from the server."""

        try:
            await ctx.guild.unban(
                user, reason=f"{reason} {ctx.author} ({ctx.author.id})"
            )
        except NotFound:
            return await ctx.warn(f"{user} is not banned")

        await Case.create(ctx, user, Action.UNBAN, reason)
        return await ctx.add_check()

    @unban.command(name="all", aliases=("everyone",))
    @has_permissions(administrator=True)
    @max_concurrency(1, BucketType.guild)
    async def unban_all(self, ctx: Context, *, reason: str = DEFAULT_REASON) -> Message:
        """Unban all banned users from the server."""

        if not ctx.author.id == ctx.guild.owner_id:
            return await ctx.warn("You must be the server owner to unban all users")

        async with ctx.typing():
            users = [entry.user async for entry in ctx.guild.bans()]
            if not users:
                return await ctx.warn("No members are currently banned")

        await ctx.prompt(f"Are you sure you want to unban {plural(users, '`'):member}?")
        async with ctx.typing(), FailureManager(max_failures=10) as manager:
            for user in users:
                await manager.attempt(
                    ctx.guild.unban(
                        user,
                        reason=f"UNBAN ALL / {reason} {ctx.author} ({ctx.author.id})",
                    )
                )

        await Case.create(ctx, ctx.guild, Action.UNBAN_ALL, reason)
        return await ctx.approve(
            f"Successfully unbanned {plural(manager.successes, '`'):member}"
        )

    @group(aliases=("lock",), invoke_without_command=True)
    @has_permissions(manage_channels=True)
    async def lockdown(
        self,
        ctx: Context,
        channel: TextChannel | Thread = parameter(default=lambda ctx: ctx.channel),
        *,
        reason: str = DEFAULT_REASON,
    ) -> Optional[Message]:
        """Prevent members from sending messages in a channel."""

        if isinstance(channel, Thread):
            config = cast(Optional[Configuration], self.bot.get_cog("Configuration"))
            if not config:
                return await ctx.send(
                    "This command is not available for threads at the moment"
                )

            return await config.thread_archive(ctx, thread=channel)

        elif not isinstance(channel, TextChannel):
            return await ctx.warn("This command only works in text channels")

        overwrites = channel.overwrites_for(ctx.settings.lockdown_role)
        if overwrites.send_messages is False:
            return await ctx.warn(f"{channel.mention} is already locked")

        overwrites.send_messages = False
        await channel.set_permissions(
            ctx.settings.lockdown_role,
            overwrite=overwrites,
            reason=f"{reason} {ctx.author} ({ctx.author.id})",
        )
        await Case.create(ctx, channel, Action.LOCKDOWN, reason)
        return await ctx.add_check()

    @lockdown.command(name="all", aliases=("server",))
    @has_permissions(administrator=True)
    @max_concurrency(1, BucketType.guild)
    @cooldown(1, 60, BucketType.guild)
    async def lockdown_all(
        self,
        ctx: Context,
        *,
        reason: str = DEFAULT_REASON,
    ) -> Message:
        """Lockdown all text channels in the server."""

        if not ctx.settings.lockdown_ignore:
            await ctx.prompt(
                "Are you sure you want to lock **ALL** channels?",
                "You haven't ignored any important channels yet",
            )

        await ctx.respond("Locking down all channels...")
        locked = 0
        async with ctx.typing():
            with Stopwatch() as sw:
                for channel in ctx.guild.text_channels:
                    overwrites = channel.overwrites_for(ctx.settings.lockdown_role)
                    if (
                        overwrites.send_messages is False
                        or channel in ctx.settings.lockdown_ignore
                    ):
                        continue

                    overwrites.send_messages = False
                    await channel.set_permissions(
                        ctx.settings.lockdown_role,
                        overwrite=overwrites,
                        reason=f"SERVER LOCKDOWN / {reason} {ctx.author} ({ctx.author.id})",
                    )
                    locked += 1

        await Case.create(ctx, ctx.guild, Action.SERVER_LOCKDOWN, reason)
        return await ctx.approve(
            f"Successfully locked down {plural(locked, '`'):channel} in `{sw.elapsed:.2f}s`",
            delete_response=True,
        )

    @lockdown.group(name="role", aliases=("set",), invoke_without_command=True)
    @has_permissions(administrator=True)
    async def lockdown_role(
        self,
        ctx: Context,
        *,
        role: Annotated[
            Role,
            StrictRole(check_integrated=False, allow_default=True),
        ],
    ) -> Message:
        """Set the role that will be overridden during lockdown."""

        await ctx.settings.upsert(lockdown_role_id=role.id)
        return await ctx.approve(f"Now overriding {role.mention} during lockdown")

    @lockdown_role.command(name="remove", aliases=("delete", "del", "rm", "reset"))
    @has_permissions(administrator=True)
    async def lockdown_role_remove(self, ctx: Context) -> Message:
        """Reset the role to the default during lockdown."""

        return await self.lockdown_role(ctx, role=ctx.guild.default_role)

    @lockdown.group(name="ignore", aliases=("exempt",), invoke_without_command=True)
    @has_permissions(administrator=True)
    async def lockdown_ignore(self, ctx: Context, *, channel: TextChannel) -> Message:
        """Exempt a channel from being unintentionally ~~un~~locked down."""

        if channel in ctx.settings.lockdown_ignore:
            return await ctx.warn(f"{channel.mention} is already exempt from lockdown")

        ctx.settings.record["lockdown_ignore"].append(channel.id)
        await ctx.settings.upsert()
        return await ctx.approve(f"Now exempting {channel.mention} from lockdown")

    @lockdown_ignore.command(name="remove", aliases=("delete", "del", "rm"))
    @has_permissions(administrator=True)
    async def lockdown_ignore_remove(
        self,
        ctx: Context,
        *,
        channel: TextChannel,
    ) -> Message:
        """Remove a channel from the lockdown exemption list."""

        if channel not in ctx.settings.lockdown_ignore:
            return await ctx.warn(f"{channel.mention} is not exempt from lockdown")

        ctx.settings.record["lockdown_ignore"].remove(channel.id)
        await ctx.settings.upsert()
        return await ctx.approve(f"No longer exempting {channel.mention} from lockdown")

    @lockdown_ignore.command(name="list")
    async def lockdown_ignore_list(self, ctx: Context) -> Message:
        """View the channels that are exempt from lockdown."""

        if not ctx.settings.lockdown_ignore:
            return await ctx.warn("No channels are exempt from lockdown")

        channels = [
            f"{channel.mention} [`{channel.id}`]"
            for channel in ctx.settings.lockdown_ignore
        ]
        embed = Embed(title="Exempt Channels")

        paginator = Paginator(ctx, channels, embed)
        return await paginator.start()

    @group(aliases=("unlock",), invoke_without_command=True)
    @has_permissions(manage_channels=True)
    async def unlockdown(
        self,
        ctx: Context,
        channel: TextChannel | Thread = parameter(default=lambda ctx: ctx.channel),
        *,
        reason: str = DEFAULT_REASON,
    ) -> Optional[Message]:
        """Allow members to send messages in a channel."""

        if isinstance(channel, Thread):
            config = cast(Optional[Configuration], self.bot.get_cog("Configuration"))
            if not config:
                return await ctx.send(
                    "This command is not available for threads at the moment"
                )

            return await config.thread_unarchive(ctx, thread=channel)

        elif not isinstance(channel, TextChannel):
            return await ctx.warn("This command only works in text channels")

        overwrites = channel.overwrites_for(ctx.settings.lockdown_role)
        if overwrites.send_messages is True:
            return await ctx.warn(f"{channel.mention} is already unlocked")

        overwrites.send_messages = True
        await channel.set_permissions(
            ctx.settings.lockdown_role,
            overwrite=overwrites,
            reason=f"{reason} {ctx.author} ({ctx.author.id})",
        )
        await Case.create(ctx, channel, Action.UNLOCKDOWN, reason)
        return await ctx.add_check()

    @unlockdown.command(name="all", aliases=("server",))
    @has_permissions(administrator=True)
    @max_concurrency(1, BucketType.guild)
    @cooldown(1, 60, BucketType.guild)
    async def unlockdown_all(
        self,
        ctx: Context,
        *,
        reason: str = DEFAULT_REASON,
    ) -> Message:
        """Unlock all text channels in the server."""

        if not ctx.settings.lockdown_ignore:
            await ctx.prompt(
                "Are you sure you want to unlock **ALL** channels?",
                "You haven't ignored any important channels yet",
            )

        await ctx.respond("Unlocking all channels...")
        unlocked = 0
        async with ctx.typing():
            with Stopwatch() as sw:
                for channel in ctx.guild.text_channels:
                    overwrites = channel.overwrites_for(ctx.settings.lockdown_role)
                    if (
                        overwrites.send_messages is True
                        or channel in ctx.settings.lockdown_ignore
                    ):
                        continue

                    overwrites.send_messages = True
                    await channel.set_permissions(
                        ctx.settings.lockdown_role,
                        overwrite=overwrites,
                        reason=f"SERVER UNLOCKDOWN / {reason} {ctx.author} ({ctx.author.id})",
                    )
                    unlocked += 1

        await Case.create(ctx, ctx.guild, Action.SERVER_UNLOCKDOWN, reason)
        return await ctx.approve(
            f"Successfully unlocked {plural(unlocked, '`'):channel} in `{sw.elapsed:.2f}s`",
            delete_response=True,
        )

    @group(aliases=("slowmo", "slow"), invoke_without_command=True)
    @has_permissions(manage_channels=True)
    async def slowmode(
        self,
        ctx: Context,
        channel: Optional[TextChannel] = parameter(default=lambda ctx: ctx.channel),
        delay: timedelta = parameter(
            converter=Duration(
                min=timedelta(seconds=0),
                max=timedelta(hours=6),
            ),
        ),
        *,
        reason: str = DEFAULT_REASON,
    ) -> Message:
        """Set the slowmode duration for a channel."""

        if not isinstance(channel, TextChannel):
            return await ctx.warn("This command only works in text channels")

        if channel.slowmode_delay == delay.seconds:
            return await ctx.warn(
                f"{channel.mention} already has a slowmode of `{precisedelta(delay)}`"
            )

        await channel.edit(
            slowmode_delay=delay.seconds,
            reason=f"{reason} {ctx.author} ({ctx.author.id})",
        )
        await Case.create(ctx, channel, Action.SLOWMODE, reason)
        return await ctx.approve(
            f"{channel.mention} now has a slowmode of `{precisedelta(delay)}`"
        )

    @slowmode.command(
        name="disable",
        aliases=("remove", "delete", "del", "rm", "reset", "off"),
    )
    @has_permissions(manage_channels=True)
    async def slowmode_disable(
        self,
        ctx: Context,
        channel: TextChannel = parameter(default=lambda ctx: ctx.channel),
    ) -> Message:
        """Disable slowmode in a channel."""

        if channel.slowmode_delay == 0:
            return await ctx.warn(f"{channel.mention} already has slowmode disabled")

        await channel.edit(
            slowmode_delay=0,
            reason=f"SLOWMODE DISABLED {ctx.author} ({ctx.author.id})",
        )
        await Case.create(ctx, channel, Action.SLOWMODE_DISABLE, DEFAULT_REASON)
        return await ctx.approve(f"Diabled slowmode in {channel.mention}")

    @command(aliases=("mvall", "mv"))
    @has_permissions(manage_channels=True)
    @max_concurrency(1, BucketType.member)
    @cooldown(1, 10, BucketType.member)
    async def moveall(
        self,
        ctx: Context,
        *,
        channel: VoiceChannel | StageChannel,
    ) -> Message:
        """Move all members to another voice channel."""

        if not ctx.author.voice or not ctx.author.voice.channel:
            return await ctx.warn("You aren't in a voice channel")

        elif ctx.author.voice.channel == channel:
            return await ctx.warn(f"You're already connected to {channel.mention}!")

        members = ctx.author.voice.channel.members
        async with ctx.typing(), FailureManager(max_failures=10) as manager:
            for member in members[:50]:
                await manager.attempt(
                    member.move_to(
                        channel,
                        reason=f"{ctx.author} moved all members",
                    )
                )

        return await ctx.approve(
            f"Moved `{manager.successes}`/`{len(members)}` member{'s' if manager.successes > 1 else ''} to {channel.mention}"
        )

    @group(aliases=("nick", "n"), invoke_without_command=True)
    @has_permissions(manage_nicknames=True)
    async def nickname(
        self,
        ctx: Context,
        member: Annotated[Member, HierarchyMember],
        *,
        nickname: Range[str, 1, 32],
    ) -> Optional[Message]:
        """Change a member's nickname."""

        key = forcenick_key(ctx.guild.id, member.id)
        if await self.bot.redis.exists(key):
            return await ctx.warn(
                f"{member.mention} currently has a forced nickname",
                f"Use `{ctx.clean_prefix}{ctx.invoked_with} remove {member}` to remove it",
            )

        await member.edit(nick=nickname, reason=f"{ctx.author} ({ctx.author.id})")
        return await ctx.add_check()

    @nickname.command(name="remove", aliases=("delete", "del", "rm", "reset"))
    @has_permissions(manage_nicknames=True)
    async def nickname_remove(
        self,
        ctx: Context,
        member: Annotated[Member, HierarchyMember],
    ) -> Message:
        """Remove a member's nickname."""

        if not member.nick:
            return await ctx.warn(f"{member.mention} doesn't have a nickname")

        key = forcenick_key(ctx.guild.id, member.id)
        forced = await self.bot.redis.delete(key)
        await member.edit(nick=None, reason=f"{ctx.author} ({ctx.author.id})")

        return await ctx.approve(
            f"Removed {member.mention}'s {'forced ' if forced else ''}nickname"
        )

    @nickname.group(
        name="force",
        aliases=("lock", "freeze"),
        invoke_without_command=True,
    )
    @has_permissions(manage_nicknames=True)
    async def nickname_force(
        self,
        ctx: Context,
        member: Annotated[Member, HierarchyMember],
        *,
        nickname: Range[str, 1, 32],
    ) -> None:
        """Force a member to have a specific nickname.

        If the member changes their nickname, it will be set back automatically."""

        key = forcenick_key(ctx.guild.id, member.id)
        await self.bot.redis.set(key, nickname)
        await member.edit(nick=nickname, reason=f"{ctx.author} ({ctx.author.id})")

        return await ctx.add_check()

    @nickname_force.command(name="cancel", aliases=("stop",))
    @has_permissions(manage_nicknames=True)
    async def nickname_force_cancel(
        self,
        ctx: Context,
        member: Annotated[Member, HierarchyMember],
    ) -> Message:
        """Cancel a forced nickname for a member."""

        key = forcenick_key(ctx.guild.id, member.id)
        if not await self.bot.redis.exists(key):
            return await ctx.warn(f"{member.mention} doesn't have a forced nickname")

        if member.nick:
            await member.edit(nick=None, reason=f"{ctx.author} ({ctx.author.id})")

        return await ctx.approve(
            f"No longer forcing {member.mention} to have a nickname"
        )

    @group(invoke_without_command=True)
    @has_permissions(manage_guild=True, kick_members=True)
    @max_concurrency(1, BucketType.guild)
    async def prune(
        self,
        ctx: Context,
        roles: Greedy[Role],
        days: Literal[7, 30] = 7,
    ) -> Message:
        """Kick members which haven't opened Discord recently."""

        estimated_members = await ctx.guild.estimate_pruned_members(
            days=days,
            roles=roles,
        )
        if not estimated_members:
            return await ctx.warn(
                f"No members have been inactive for `{days}` days",
                "You might have to specify member roles to include",
            )

        await ctx.prompt(
            f"Are you sure you want to prune {plural(estimated_members, '`'):member}?",
            "This action cannot be cancelled or undone",
        )
        async with ctx.typing():
            pruned = (
                await ctx.guild.prune_members(
                    days=days,
                    roles=roles,
                    compute_prune_count=not ctx.guild.large,
                    reason=f"PRUNE / {ctx.author} ({ctx.author.id})",
                )
                or estimated_members
            )

        return await ctx.approve(
            f"Successfully pruned {plural(pruned, '`'):inactive member}"
        )

    @prune.command(name="invites", aliases=("links", "invs", "inv"))
    @has_permissions(manage_guild=True, kick_members=True)
    @max_concurrency(1, BucketType.guild)
    async def prune_invites(
        self,
        ctx: Context,
        uses: Range[int, 0, 100] = 0,
    ) -> Message:
        """Invalidate invites which don't have the minimum uses."""

        invites = await ctx.guild.invites()
        if not invites:
            return await ctx.warn("No invites were found in the server")

        invites = [invite for invite in invites if (invite.uses or 0) <= uses]
        if not invites:
            return await ctx.warn(
                f"No invites have `{uses}`{' or fewer' if uses != 0 else ''} uses"
            )

        await ctx.prompt(
            f"Are you sure you want to invalidate {plural(invites, '`'):invite}?",
            "This action cannot be undone or cancelled",
        )
        async with ctx.typing():
            for invite in invites:
                try:
                    await invite.delete(
                        reason=f"PRUNE INVITES / {ctx.author} ({ctx.author.id})"
                    )
                except NotFound:
                    pass

        return await ctx.approve(
            f"Successfully invalidated {plural(invites, '`'):invite} with `{uses}`{' or fewer' if uses != 0 else ''} uses"
        )


async def setup(bot: Juno):
    await bot.add_cog(Moderation(bot))
