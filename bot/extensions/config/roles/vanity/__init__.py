from contextlib import suppress
from logging import getLogger
from typing import Annotated

from discord import (
    CustomActivity,
    Embed,
    HTTPException,
    Member,
    Message,
    Role,
    TextChannel,
    Thread,
)
from discord.ext.commands import Cog, check, group, has_permissions
from discord.utils import find

from bot.core import Context, Juno
from bot.shared import codeblock
from bot.shared.converters.role import StrictRole
from bot.shared.formatter import vowel
from bot.shared.script import Script

from .settings import Settings

logger = getLogger("bot.vanity")


def get_status(member: Member) -> str:
    """Get the member's custom status."""

    activity = find(lambda a: isinstance(a, CustomActivity), member.activities)
    if activity and activity.name:
        return activity.name.lower()

    return ""


class VanityRoles(Cog):
    def __init__(self, bot: Juno) -> None:
        self.bot = bot

    @Cog.listener("on_presence_update")
    async def vanity_listener(self, before: Member, member: Member) -> None:
        """Assign or remove vanity roles based on the member's status."""

        if member.bot:
            return

        guild = member.guild
        vanity = guild.vanity_url_code
        if not vanity:
            return

        before_status = get_status(before)
        status = get_status(member)
        if status == before_status:
            return

        settings = await Settings.fetch(self.bot, guild)
        if not settings:
            return

        with suppress(HTTPException):
            has_roles = [role for role in settings.roles if role in member.roles]
            missing_roles = [
                role for role in settings.roles if role not in member.roles
            ]

            if vanity not in status and has_roles:
                await member.remove_roles(
                    *has_roles,
                    reason=f"Vanity /{vanity} no longer in status",
                    atomic=False,
                )

            elif vanity in status and missing_roles:
                await member.add_roles(
                    *missing_roles,
                    reason=f"Vanity /{vanity} now in status",
                    atomic=False,
                )

                await settings.dispatch_notification(before, member)

    @group(aliases=("vr",), invoke_without_command=True)
    @has_permissions(manage_roles=True)
    @check(lambda ctx: bool(ctx.guild and ctx.guild.vanity_url_code))
    async def vanity(self, ctx: Context) -> Message:
        """Award members for advertising the server."""

        return await ctx.send_help(ctx.command)

    @vanity.command(name="settings", aliases=("config", "cfg"))
    @has_permissions(manage_guild=True)
    @check(lambda ctx: bool(ctx.guild and ctx.guild.vanity_url_code))
    async def vanity_settings(self, ctx: Context) -> Message:
        """View the current vanity settings."""

        settings = await Settings.fetch(self.bot, ctx.guild)
        if not settings:
            return await ctx.warn("No vanity settings have been configured")

        embed = Embed(title=f"Vanity Settings /{ctx.guild.vanity_url_code}")
        embed.description = "\n".join(
            [
                f"**Role{'s' if len(settings.roles) != 1 else ''}:** {', '.join(role.mention for role in settings.roles) or 'N/A'}",
                f"**Channel:** {settings.channel.mention if settings.channel else 'N/A'}",
            ]
        )
        if settings.template:
            embed.add_field(
                name="Script Template",
                value=codeblock(settings.template, "yaml"),
            )

        return await ctx.send(embed=embed)

    @vanity.command(name="role", aliases=("roles", "r"))
    @has_permissions(manage_roles=True)
    @check(lambda ctx: bool(ctx.guild and ctx.guild.vanity_url_code))
    async def vanity_role(
        self,
        ctx: Context,
        *,
        role: Annotated[
            Role,
            StrictRole(check_dangerous=True),
        ],
    ) -> Message:
        """Add or remove a role to be granted."""

        settings = await Settings.fetch(self.bot, ctx.guild)
        if role in settings.roles:
            await ctx.prompt(
                f"{role.mention} is already being granted to members",
                "Would you like to no longer grant this role?",
            )
            settings.role_ids.remove(role.id)
        else:
            settings.role_ids.append(role.id)

        await settings.upsert()
        return await ctx.approve(
            f"{'Now' if role in settings.roles else 'No longer'} granting {role.mention} to advertisers"
        )

    @vanity.group(name="channel", aliases=("logs",), invoke_without_command=True)
    @has_permissions(manage_channels=True)
    @check(lambda ctx: bool(ctx.guild and ctx.guild.vanity_url_code))
    async def vanity_channel(
        self, ctx: Context, *, channel: TextChannel | Thread
    ) -> Message:
        """Set the channel to send award notifications."""

        settings = await Settings.fetch(self.bot, ctx.guild)
        if settings.channel_id == channel.id:
            return await ctx.warn(
                f"{channel.mention} is already the notification channel"
            )

        await settings.upsert(channel_id=channel.id)
        return await ctx.approve(
            f"Vanity notifications will now be sent to {channel.mention}"
        )

    @vanity_channel.command(name="remove", aliases=("delete", "del", "rm"))
    @has_permissions(manage_channels=True)
    @check(lambda ctx: bool(ctx.guild and ctx.guild.vanity_url_code))
    async def vanity_channel_remove(self, ctx: Context) -> Message:
        """Remove the notification channel."""

        settings = await Settings.fetch(self.bot, ctx.guild)
        if not settings.channel:
            return await ctx.warn("No notification channel has been set")

        await settings.upsert(channel_id=None)
        return await ctx.approve("No longer sending vanity notifications")

    @vanity.group(
        name="message",
        aliases=("template", "msg"),
        invoke_without_command=True,
    )
    @has_permissions(manage_messages=True)
    @check(lambda ctx: bool(ctx.guild and ctx.guild.vanity_url_code))
    async def vanity_message(self, ctx: Context, *, script: Script) -> Message:
        """Set the message to send after awarding a role.

        The following variables are available:
        > `{role}`: The first role being granted.
        > `{vanity}`: The vanity URL code without the slash."""

        settings = await Settings.fetch(self.bot, ctx.guild)

        await settings.upsert(template=script.template)
        return await ctx.approve(
            f"Now using {vowel(script.format)} message for vanity notifications"
        )

    @vanity_message.command(name="remove", aliases=("delete", "del", "rm"))
    @has_permissions(manage_messages=True)
    @check(lambda ctx: bool(ctx.guild and ctx.guild.vanity_url_code))
    async def vanity_message_remove(self, ctx: Context) -> Message:
        """Remove the award message."""

        settings = await Settings.fetch(self.bot, ctx.guild)
        if not settings.template:
            return await ctx.warn("No award message has been set")

        await settings.upsert(template=None)
        return await ctx.approve("The award message has been reset")

    @vanity.command(name="reset", aliases=("clear",))
    @has_permissions(manage_roles=True)
    @check(lambda ctx: bool(ctx.guild and ctx.guild.vanity_url_code))
    async def vanity_reset(self, ctx: Context) -> Message:
        """Reset the vanity role configuration."""

        await ctx.prompt(
            "Are you sure you want to reset the vanity settings?",
            "This action cannot be undone and will remove all settings",
        )

        settings = await Settings.fetch(self.bot, ctx.guild)
        await settings.upsert(
            role_ids=[],
            channel_id=None,
            template=None,
        )
        return await ctx.approve("No longer awarding members for advertising")
