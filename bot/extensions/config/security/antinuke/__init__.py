from datetime import timedelta
from logging import getLogger
from time import time
from typing import Annotated, Optional

from discord import AuditLogEntry, Embed, HTTPException, Member, Message, User
from discord.ext.commands import Cog, CommandError, Range, flag, group
from humanize import naturaldelta

from bot.core import Context, Juno
from bot.shared.converters import FlagConverter, Status
from bot.shared.converters.time import Duration
from bot.shared.formatter import plural
from bot.shared.paginator import Paginator
from config import config

from .settings import Module, Settings

logger = getLogger("bot.antinuke")
worker_id = config.antinuke_worker.id if config.antinuke_worker else 0


async def is_trusted(ctx: Context) -> bool:
    """Check if the invoker is trusted."""

    if not ctx.command.qualified_name.startswith("antinuke"):
        return True

    settings = await Settings.fetch(ctx.bot, ctx.guild)
    if settings.is_manager(ctx.author):
        return True

    raise CommandError("You are not the owner of this server")


class Flags(FlagConverter):
    threshold: Range[int, 1, 12] = flag(
        default=3,
        aliases=["limit"],
        description="The threshold for the module to trigger.",
    )
    duration: timedelta = flag(
        aliases=["time", "per"],
        converter=Duration(
            max=timedelta(hours=12),
        ),
        default=timedelta(minutes=60),
        description="The duration before the threshold resets.",
    )


class Antinuke(Cog):
    def __init__(self, bot: Juno):
        self.bot = bot

    async def cog_load(self) -> None:
        self.bot.add_check(is_trusted)
        return await super().cog_load()

    async def cog_unload(self) -> None:
        self.bot.remove_check(is_trusted)
        return await super().cog_unload()

    @group(aliases=("antiwizz", "an", "aw"), invoke_without_command=True)
    async def antinuke(self, ctx: Context) -> Message:
        """Protect your server from malicious users."""

        return await ctx.send_help(ctx.command)

    @antinuke.command(name="settings", aliases=("config", "cfg"))
    async def antinuke_settings(self, ctx: Context) -> Message:
        """View the current antinuke protection settings."""

        settings = await Settings.fetch(ctx.bot, ctx.guild)
        if not settings:
            return await ctx.warn("No settings have been configured for the antinuke")

        embed = Embed(title="Antinuke Settings")
        embed.description = (
            "Once a threshold is reached, the user will be banned from the server\n"
            "> Afterwards, the incident will be logged and reverted if necessary"
        )

        module_config: Module
        if any(getattr(settings, module) for module in ("ban", "kick")):
            embed.add_field(
                name="Member Protection",
                value="\n".join(
                    f"{module.title()}s are limited to `{module_config['threshold']}` in {naturaldelta(timedelta(seconds=module_config['duration']))}"
                    for module in ("ban", "kick")
                    if (module_config := settings.record.get(module))  # type: ignore
                ),
            )

        if any(getattr(settings, module) for module in ("role", "channel")):
            embed.add_field(
                name="Server Protection",
                value="\n".join(
                    f"{module.title()}s are limited to `{module_config['threshold']}` in {naturaldelta(timedelta(seconds=module_config['duration']))}"
                    for module in ("role", "channel")
                    if (module_config := settings.record.get(module))  # type: ignore
                ),
            )

        if any(getattr(settings, module) for module in ("bot_add", "webhook", "emoji")):
            modules: list[str] = []
            if settings.bot_add:
                modules.append("Bot can only be added by whitelisted members")

            if settings.emoji:
                modules.append(
                    f"Emojis are limited to `{settings.emoji['threshold']}` in {naturaldelta(timedelta(seconds=settings.emoji['duration']))}"
                )

            if settings.webhook:
                modules.append(
                    f"Webhooks are limited to `{settings.webhook['threshold']}` in {naturaldelta(timedelta(seconds=settings.webhook['duration']))}"
                )

            embed.add_field(
                name="Miscellaneous",
                value="\n".join(modules),
                inline=False,
            )

        if settings.vanity and ctx.guild.vanity_url:
            worker = ctx.guild.get_member(worker_id)
            embed.add_field(
                name="Vanity Protection",
                value=f"The perpertrator will be punished, and the vanity `{ctx.guild.vanity_url_code}` will be reset through {worker.mention} if changed"
                if worker and worker.guild_permissions.administrator
                else f"The perpertrator will be punished, but the vanity `{ctx.guild.vanity_url_code}` will not be reverted\n-# The worker isn't present or doesn't have the necessary permissions",
            )

        return await ctx.send(embed=embed)

    @antinuke.group(name="whitelist", aliases=("wl",), invoke_without_command=True)
    async def antinuke_whitelist(self, ctx: Context, *, user: Member | User) -> Message:
        """Exclude a user from being affected by the antinuke."""

        settings = await Settings.fetch(ctx.bot, ctx.guild)
        if user.id in settings.whitelist:
            await ctx.prompt(
                f"{user.mention} is already whitelisted",
                "Would you like to unwhitelist them?",
            )
            settings.whitelist.remove(user.id)
        else:
            settings.whitelist.append(user.id)

        await settings.upsert()
        return await ctx.approve(
            f"{'Now' if user.id in settings.whitelist else 'No longer'} excluding {user.mention} from being punished"
        )

    @antinuke_whitelist.command(name="list", aliases=("view",))
    async def antinuke_whitelist_list(self, ctx: Context) -> Message:
        """View users that are excluded from the antinuke."""

        settings = await Settings.fetch(ctx.bot, ctx.guild)
        members = [
            f"{self.bot.get_user(user_id) or 'Unknown User'} [`{user_id}`]"
            for user_id in settings.whitelist
        ]
        if not members:
            return await ctx.warn("No members are excluded from the antinuke")

        embed = Embed(title="Whitelisted Users")
        paginator = Paginator(ctx, members, embed)
        return await paginator.start()

    @antinuke_whitelist.command(name="clear", aliases=("reset",))
    async def antinuke_whitelist_clear(self, ctx: Context) -> Message:
        """Remove all users from the antinuke whitelist."""

        settings = await Settings.fetch(ctx.bot, ctx.guild)
        if not settings.whitelist:
            return await ctx.warn("No users are excluded from the antinuke")

        await ctx.prompt("Are you sure you want to clear all whitelisted users?")
        settings.whitelist.clear()
        await settings.upsert()
        return await ctx.approve("Removed all users from the antinuke whitelist")

    @antinuke.group(
        name="trust",
        aliases=("manager", "mod", "admin"),
        invoke_without_command=True,
    )
    async def antinuke_trust(self, ctx: Context, *, member: Member) -> Message:
        """Allow a member to manage the antinuke settings."""

        settings = await Settings.fetch(ctx.bot, ctx.guild)
        if member.id in settings.managers:
            await ctx.prompt(
                f"{member.mention} is already a manager",
                "Would you like to revoke their permissions?",
            )
            settings.managers.remove(member.id)
        else:
            await ctx.prompt(
                f"Are you sure you want to trust {member.mention}?",
                "They will be able to manage the antinuke settings entirely",
            )
            settings.managers.append(member.id)

        await settings.upsert()
        return await ctx.approve(
            f"{'Now' if member.id in settings.managers else 'No longer'} trusting {member.mention} to manage the antinuke"
        )

    @antinuke_trust.command(name="list", aliases=("view",))
    async def antinuke_trust_list(self, ctx: Context) -> Message:
        """View members that are trusted to manage the antinuke."""

        settings = await Settings.fetch(ctx.bot, ctx.guild)
        members = [
            f"{self.bot.get_user(user_id) or 'Unknown User'} [`{user_id}`]"
            for user_id in settings.managers
        ]
        if not members:
            return await ctx.warn("No members are trusted to manage the antinuke")

        embed = Embed(title="Trusted Members")
        paginator = Paginator(ctx, members, embed)
        return await paginator.start()

    @antinuke_trust.command(name="clear", aliases=("reset",))
    async def antinuke_trust_clear(self, ctx: Context) -> Message:
        """Remove all members from the antinuke managers."""

        settings = await Settings.fetch(ctx.bot, ctx.guild)
        if not settings.managers:
            return await ctx.warn("No members are trusted to manage the antinuke")

        await ctx.prompt("Are you sure you want to clear all trusted members?")
        settings.managers.clear()
        await settings.upsert()
        return await ctx.approve("Removed all members from the antinuke managers")

    @antinuke.command(name="bot", aliases=("bots", "botadd"))
    async def antinuke_bot(
        self,
        ctx: Context,
        status: Annotated[bool, Status],
    ) -> Message:
        """Prevent bots from being added to the server.

        Whitelisted members are exempt from this setting.
        """

        settings = await Settings.fetch(ctx.bot, ctx.guild)
        if settings.bot_add == status:
            return await ctx.warn(
                f"Protection against bots being added is already {'enabled' if status else 'disabled'}"
            )

        await settings.upsert(bot_add=status)
        return await ctx.approve(
            f"Bots {'can no longer' if status else 'can now'} be added to the server"
        )

    @antinuke.command(name="vanity", aliases=("vanityurl",), hidden=True)
    async def antinuke_vanity(
        self,
        ctx: Context,
        status: Annotated[bool, Status],
    ) -> Message:
        """Prevent the vanity URL from being changed.

        The worker must be present and have the necessary permissions to reset the vanity URL."""

        settings = await Settings.fetch(ctx.bot, ctx.guild)
        if settings.vanity == status:
            return await ctx.warn(
                f"Protection against the vanity URL being changed is already {'enabled' if status else 'disabled'}"
            )

        await settings.upsert(vanity=status)
        if not status:
            return await ctx.approve(
                "Protection against the vanity URL being changed is now disabled"
            )

        worker = ctx.guild.get_member(worker_id)
        if not worker:
            return await ctx.warn(
                "The worker is not present, and the vanity URL will not be reset if changed"
            )

        return await ctx.approve(
            "Now protecting the vanity URL from being changed",
            f"The vanity URL will be reset through {worker.mention} if changed"
            if worker and worker.guild_permissions.administrator
            else "The worker is not present, and the vanity URL will not be reset if changed",
        )

    @antinuke.command(name="ban", aliases=("bans",))
    async def antinuke_ban(
        self,
        ctx: Context,
        status: Annotated[bool, Status],
        *,
        flags: Flags,
    ) -> Message:
        """Prevent members from being banned from the server."""

        settings = await Settings.fetch(ctx.bot, ctx.guild)
        if not settings.ban and not status:
            return await ctx.warn(
                "Protection against members being banned is already disabled"
            )

        settings.record["ban"] = None
        if status:
            settings.record["ban"] = {
                "threshold": flags.threshold,
                "duration": int(flags.duration.total_seconds()),
            }

        await settings.upsert()
        if not status:
            return await ctx.approve(
                "Protection against members being banned is now disabled"
            )

        return await ctx.approve(
            f"Perpetrators will now be punished if {plural(flags.threshold, md='`'):member is|members are} banned within {naturaldelta(flags.duration)}"
        )

    @antinuke.command(name="kick", aliases=("kicks",))
    async def antinuke_kick(
        self,
        ctx: Context,
        status: Annotated[bool, Status],
        *,
        flags: Flags,
    ) -> Message:
        """Prevent members from being kicked from the server."""

        settings = await Settings.fetch(ctx.bot, ctx.guild)
        if not settings.kick and not status:
            return await ctx.warn(
                "Protection against members being kicked is already disabled"
            )

        settings.record["kick"] = None
        if status:
            settings.record["kick"] = {
                "threshold": flags.threshold,
                "duration": int(flags.duration.total_seconds()),
            }

        await settings.upsert()
        if not status:
            return await ctx.approve(
                "Protection against members being kicked is now disabled"
            )

        return await ctx.approve(
            f"Perpetrators will now be punished if {plural(flags.threshold, md='`'):member is|members are} kicked within {naturaldelta(flags.duration)}"
        )

    @antinuke.command(name="role", aliases=("roles",))
    async def antinuke_role(
        self,
        ctx: Context,
        status: Annotated[bool, Status],
        *,
        flags: Flags,
    ) -> Message:
        """Prevent roles from being created or deleted."""

        settings = await Settings.fetch(ctx.bot, ctx.guild)
        if not settings.role and not status:
            return await ctx.warn(
                "Protection against roles being created or deleted is already disabled"
            )

        settings.record["role"] = None
        if status:
            settings.record["role"] = {
                "threshold": flags.threshold,
                "duration": int(flags.duration.total_seconds()),
            }

        await settings.upsert()
        if not status:
            return await ctx.approve(
                "Protection against roles being created or deleted is now disabled"
            )

        return await ctx.approve(
            f"Perpetrators will now be punished if {plural(flags.threshold, md='`'):role is|roles are} modified within {naturaldelta(flags.duration)}"
        )

    @antinuke.command(name="channel", aliases=("channels",))
    async def antinuke_channel(
        self,
        ctx: Context,
        status: Annotated[bool, Status],
        *,
        flags: Flags,
    ) -> Message:
        """Prevent channels from being created or deleted."""

        settings = await Settings.fetch(ctx.bot, ctx.guild)
        if not settings.channel and not status:
            return await ctx.warn(
                "Protection against channels being created or deleted is already disabled"
            )

        settings.record["channel"] = None
        if status:
            settings.record["channel"] = {
                "threshold": flags.threshold,
                "duration": int(flags.duration.total_seconds()),
            }

        await settings.upsert()
        if not status:
            return await ctx.approve(
                "Protection against channels being created or deleted is now disabled"
            )

        return await ctx.approve(
            f"Perpetrators will now be punished if {plural(flags.threshold, md='`'):channel is|channels are} modified within {naturaldelta(flags.duration)}"
        )

    @antinuke.command(name="webhook", aliases=("webhooks",))
    async def antinuke_webhook(
        self,
        ctx: Context,
        status: Annotated[bool, Status],
        *,
        flags: Flags,
    ) -> Message:
        """Prevent webhooks from being created or deleted."""

        settings = await Settings.fetch(ctx.bot, ctx.guild)
        if not settings.webhook and not status:
            return await ctx.warn(
                "Protection against webhooks being created or deleted is already disabled"
            )

        settings.record["webhook"] = None
        if status:
            settings.record["webhook"] = {
                "threshold": flags.threshold,
                "duration": int(flags.duration.total_seconds()),
            }

        await settings.upsert()
        if not status:
            return await ctx.approve(
                "Protection against webhooks being created or deleted is now disabled"
            )

        return await ctx.approve(
            f"Perpetrators will now be punished if {plural(flags.threshold, md='`'):webhook is|webhooks are} modified within {naturaldelta(flags.duration)}"
        )

    @antinuke.command(name="emoji", aliases=("emojis",))
    async def antinuke_emoji(
        self,
        ctx: Context,
        status: Annotated[bool, Status],
        *,
        flags: Flags,
    ) -> Message:
        """Prevent emojis from being created or deleted."""

        settings = await Settings.fetch(ctx.bot, ctx.guild)
        if not settings.emoji and not status:
            return await ctx.warn(
                "Protection against emojis being created or deleted is already disabled"
            )

        settings.record["emoji"] = None
        if status:
            settings.record["emoji"] = {
                "threshold": flags.threshold,
                "duration": int(flags.duration.total_seconds()),
            }

        await settings.upsert()
        if not status:
            return await ctx.approve(
                "Protection against emojis being created or deleted is now disabled"
            )

        return await ctx.approve(
            f"Perpetrators will now be punished if {plural(flags.threshold, md='`'):emoji is|emojis are} modified within {naturaldelta(flags.duration)}"
        )

    @Cog.listener("on_audit_log_entry_ban")
    async def antinuke_monitor_ban(self, entry: AuditLogEntry) -> None:
        start = time()
        guild = entry.guild
        member = entry.target
        if not isinstance(member, Member):
            return

        settings = await Settings.fetch(self.bot, entry.guild)
        # member_key = f"antinuke:ban:{guild.id}.{member.id}"
        if not settings or not settings.ban or settings.is_whitelisted(member):
            return

        elif not await settings.exceeds_threshold("ban", member):
            # await self.bot.redis.sadd(member_key, str(member.id), ex=60)
            return

        elapsed = time() - start
        details: Optional[str] = None
        try:
            await guild.ban(
                member,
                reason=f"Antinuke - Exceeded the ban threshold of {settings.ban['threshold']} within {naturaldelta(timedelta(seconds=settings.ban['duration']))}",
            )
        except HTTPException as exc:
            details = exc.text

        await settings.dispatch_log(
            member,
            module="ban",
            elapsed=elapsed,
            details=details,
            failure=bool(details),
        )

    @Cog.listener("on_audit_log_entry_kick")
    async def antinuke_monitor_kick(self, entry: AuditLogEntry) -> None:
        start = time()
        guild = entry.guild
        member = entry.target
        if not isinstance(member, Member):
            return

        settings = await Settings.fetch(self.bot, entry.guild)
        if not settings or not settings.kick or settings.is_whitelisted(member):
            return

        elif not await settings.exceeds_threshold("kick", member):
            return

        elapsed = time() - start
        details: Optional[str] = None
        try:
            await guild.kick(
                member,
                reason=f"Antinuke - Exceeded the kick threshold of {settings.kick['threshold']} within {naturaldelta(timedelta(seconds=settings.kick['duration']))}",
            )
        except HTTPException as exc:
            details = exc.text

        await settings.dispatch_log(
            member,
            module="kick",
            elapsed=elapsed,
            details=details,
            failure=bool(details),
        )

    @Cog.listener("on_audit_log_entry_role_create")
    @Cog.listener("on_audit_log_entry_role_delete")
    @Cog.listener("on_audit_log_entry_role_update")
    async def antinuke_monitor_role(self, entry: AuditLogEntry) -> None:
        start = time()
        guild = entry.guild
        member = entry.target
        if not isinstance(member, Member):
            return

        settings = await Settings.fetch(self.bot, entry.guild)
        if not settings or not settings.role or settings.is_whitelisted(member):
            return

        elif not await settings.exceeds_threshold("role", member):
            return

        elapsed = time() - start
        details: Optional[str] = None
        try:
            await guild.ban(
                member,
                reason=f"Antinuke - Exceeded the role threshold of {settings.role['threshold']} within {naturaldelta(timedelta(seconds=settings.role['duration']))}",
            )
        except HTTPException as exc:
            details = exc.text

        await settings.dispatch_log(
            member,
            module="role",
            elapsed=elapsed,
            details=details,
            failure=bool(details),
        )

    @Cog.listener("on_audit_log_entry_channel_create")
    @Cog.listener("on_audit_log_entry_channel_delete")
    @Cog.listener("on_audit_log_entry_channel_update")
    async def antinuke_monitor_channel(self, entry: AuditLogEntry) -> None:
        start = time()
        guild = entry.guild
        member = entry.target
        if not isinstance(member, Member):
            return

        settings = await Settings.fetch(self.bot, entry.guild)
        if not settings or not settings.channel or settings.is_whitelisted(member):
            return

        elif not await settings.exceeds_threshold("channel", member):
            return

        elapsed = time() - start
        details: Optional[str] = None
        try:
            await guild.ban(
                member,
                reason=f"Antinuke - Exceeded the channel threshold of {settings.channel['threshold']} within {naturaldelta(timedelta(seconds=settings.channel['duration']))}",
            )
        except HTTPException as exc:
            details = exc.text

        await settings.dispatch_log(
            member,
            module="channel",
            elapsed=elapsed,
            details=details,
            failure=bool(details),
        )

    @Cog.listener("on_audit_log_entry_webhook_create")
    @Cog.listener("on_audit_log_entry_webhook_delete")
    async def antinuke_monitor_webhook(self, entry: AuditLogEntry) -> None:
        start = time()
        guild = entry.guild
        member = entry.target
        if not isinstance(member, Member):
            return

        settings = await Settings.fetch(self.bot, entry.guild)
        if not settings or not settings.webhook or settings.is_whitelisted(member):
            return

        elif not await settings.exceeds_threshold("webhook", member):
            return

        elapsed = time() - start
        details: Optional[str] = None
        try:
            await guild.ban(
                member,
                reason=f"Antinuke - Exceeded the webhook threshold of {settings.webhook['threshold']} within {naturaldelta(timedelta(seconds=settings.webhook['duration']))}",
            )
        except HTTPException as exc:
            details = exc.text

        await settings.dispatch_log(
            member,
            module="webhook",
            elapsed=elapsed,
            details=details,
            failure=bool(details),
        )

    @Cog.listener("on_audit_log_entry_emoji_create")
    @Cog.listener("on_audit_log_entry_emoji_delete")
    async def antinuke_monitor_emoji(self, entry: AuditLogEntry) -> None:
        start = time()
        guild = entry.guild
        member = entry.target
        if not isinstance(member, Member):
            return

        settings = await Settings.fetch(self.bot, entry.guild)
        if not settings or not settings.emoji or settings.is_whitelisted(member):
            return

        elif not await settings.exceeds_threshold("emoji", member):
            return

        elapsed = time() - start
        details: Optional[str] = None
        try:
            await guild.ban(
                member,
                reason=f"Antinuke - Exceeded the emoji threshold of {settings.emoji['threshold']} within {naturaldelta(timedelta(seconds=settings.emoji['duration']))}",
            )
        except HTTPException as exc:
            details = exc.text

        await settings.dispatch_log(
            member,
            module="emoji",
            elapsed=elapsed,
            details=details,
            failure=bool(details),
        )
