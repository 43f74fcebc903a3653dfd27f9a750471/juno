from __future__ import annotations

from asyncio import Lock
from collections import defaultdict
from contextlib import suppress
from datetime import timedelta
from io import BytesIO
from logging import getLogger
from typing import TYPE_CHECKING, Annotated, Optional, Sequence, TypedDict, cast

from discord import (
    Asset,
    AuditLogEntry,
    Color,
    DMChannel,
    Embed,
    Emoji,
    File,
    GroupChannel,
    Guild,
    GuildSticker,
    HTTPException,
    Invite,
    Member,
    Message,
    Object,
    PartialMessageable,
    Role,
    TextChannel,
    Thread,
    User,
    VoiceState,
)
from discord.abc import GuildChannel
from discord.ext.commands import Cog, Greedy, group, has_permissions
from discord.ext.tasks import loop
from discord.utils import MISSING, as_chunks, format_dt, utcnow
from humanfriendly import format_timespan

from bot.core import Context, Juno
from bot.shared.formatter import human_join, plural
from bot.shared.paginator import Paginator

from .enums import LogType

if TYPE_CHECKING:
    from ...moderation.history.case import Case

logger = getLogger("bot.logging")


class Record(TypedDict):
    guild_id: int
    channel_id: int
    events: list[str]


queued_messages: dict[TextChannel | Thread, list[Embed]] = defaultdict(list)
queue_lock = Lock()


async def log(
    event: LogType,
    guild: Guild,
    embed: Optional[Embed] = None,
    *,
    case: Optional[Case] = None,
    user: Optional[Member | User] = None,
    files: Sequence[File] = MISSING,
) -> Optional[Message]:
    """Send a log to the appropriate channel."""

    bot = cast(Juno, guild._state._get_client())
    if not guild.me:
        return

    query = """
    SELECT channel_id
    FROM logging
    WHERE guild_id = $1
    AND $2 = ANY(events)
    """
    channel_id = cast(
        Optional[int],
        await bot.db.fetchval(query, guild.id, event.name),
    )
    if not channel_id:
        return

    channel = cast(
        Optional[TextChannel | Thread],
        guild.get_channel_or_thread(channel_id),
    )
    if not channel:
        await bot.db.execute(
            "DELETE FROM logging WHERE guild_id = $1 AND channel_id = $2",
            guild.id,
            channel_id,
        )
        return

    elif not all(
        (
            channel.permissions_for(guild.me).send_messages,
            channel.permissions_for(guild.me).embed_links,
            channel.permissions_for(guild.me).attach_files,
        )
    ):
        return

    if case and not embed:
        embed = await case.embed()

    elif not embed:
        return

    if user and not embed.author:
        embed.set_author(name=user, icon_url=user.display_avatar)
        if not embed.footer:
            embed.set_footer(text=f"{user.__class__.__name__} ID: {user.id}")

    if not embed.timestamp:
        embed.timestamp = utcnow()

    if files:
        with suppress(HTTPException):
            return await channel.send(embed=embed, files=files, silent=True)

        return

    async with queue_lock:
        queued_messages[channel].append(embed)

    logger.info(
        f"Queued {event.name} log for {guild} in {channel} / {plural(len(queued_messages[channel])):message} queued"
    )


class Logging(Cog):
    def __init__(self, bot: Juno):
        self.bot = bot

    async def cog_load(self) -> None:
        self.send_queued_log_messages.start()
        return await super().cog_load()

    async def cog_unload(self) -> None:
        self.send_queued_log_messages.cancel()
        return await super().cog_unload()

    @loop(seconds=5)
    async def send_queued_log_messages(self):
        if not queued_messages:
            return

        async with queue_lock:
            total_messages = sum(map(len, queued_messages.values()))
            if total_messages > 50:
                logger.warning(
                    f"Dispatching {sum(map(len, queued_messages.values()))} queued log messages"
                )

            for channel, records in queued_messages.items():
                with suppress(HTTPException):
                    for chunk in as_chunks(records, 10):
                        await channel.send(embeds=chunk, silent=True)

        queued_messages.clear()

    @group(aliases=("log", "logs"), invoke_without_command=True)
    @has_permissions(manage_guild=True)
    async def logging(self, ctx: Context) -> Message:
        """Configure logging for certain events."""

        return await ctx.send_help(ctx.command)

    @logging.command(
        name="enable",
        aliases=(
            "set",
            "on",
        ),
    )
    @has_permissions(manage_guild=True)
    async def logging_enable(
        self,
        ctx: Context,
        channel: TextChannel | Thread,
        events: Annotated[list[LogType], Greedy[LogType]],
    ) -> Message:
        """Enable logging in a channel for certain events."""

        if not events:
            events.extend(LogType.all())

        query = "SELECT * FROM logging WHERE guild_id = $1 AND channel_id = $2"
        record = cast(
            Optional[Record],
            await self.bot.db.fetchrow(query, ctx.guild.id, channel.id),
        )
        if record:
            for event in record["events"]:
                event = LogType.from_str(event)
                if event not in events:
                    events.append(event)

        query = """
        INSERT INTO logging (guild_id, channel_id, events)
        VALUES ($1, $2, $3) ON CONFLICT (guild_id, channel_id) DO UPDATE
        SET events = EXCLUDED.events
        """
        await self.bot.db.execute(
            query,
            ctx.guild.id,
            channel.id,
            [event.name for event in events],
        )

        if events == LogType.all():
            return await ctx.approve(f"Now logging all events in {channel.mention}")

        if len(events) <= 2:
            human_events = human_join(
                [f"`{event}`" for event in events],
                final="and",
            )
            return await ctx.approve(
                f"Now logging {human_events} events in {channel.mention}"
            )

        return await ctx.approve(
            f"Now logging {plural(len(events)):event} in {channel.mention}"
        )

    @logging.command(name="disable", aliases=("delete", "remove", "del", "rm", "off"))
    @has_permissions(manage_guild=True)
    async def logging_disable(
        self,
        ctx: Context,
        channel: TextChannel | Thread,
        events: Annotated[list[LogType], Greedy[LogType]],
    ) -> Message:
        """Remove logging for a channel.

        If no events are provided, all events will be removed.
        """

        if events:
            query = "SELECT * FROM logging WHERE guild_id = $1"
            record = cast(
                Optional[Record],
                await self.bot.db.fetchrow(query, ctx.guild.id),
            )
            if not record:
                return await ctx.warn("There are no logging channels set up")

            new_events = [LogType.from_str(event) for event in record["events"]]
            for event in events:
                if event in new_events:
                    new_events.remove(event)

            query = """
            UPDATE logging
            SET events = $3
            WHERE guild_id = $1 
            AND channel_id = $2
            """
            result = await self.bot.db.execute(
                query,
                ctx.guild.id,
                channel.id,
                [str(event) for event in new_events],
            )
            if result == "UPDATE 0":
                return await ctx.warn(
                    f"{channel.mention} is not set up to log the provided events"
                )

            if len(new_events) <= 2:
                human_events = human_join(
                    [f"`{event}`" for event in events],
                    final="and",
                )
                return await ctx.approve(
                    f"No longer logging {human_events} events in {channel.mention}"
                )

            return await ctx.approve(
                f"No longer logging {plural(len(events)):event} in {channel.mention}"
            )

        query = "DELETE FROM logging WHERE guild_id = $1 AND channel_id = $2"
        result = await self.bot.db.execute(query, ctx.guild.id, channel.id)
        if result == "DELETE 0":
            return await ctx.warn("This channel is not set up to log any events")

        return await ctx.approve(f"No longer logging in {channel.mention}")

    @logging.command(name="view", aliases=("events",))
    @has_permissions(manage_guild=True)
    async def logging_view(
        self,
        ctx: Context,
        channel: TextChannel | Thread,
    ) -> Message:
        """View all events being logged in a channel."""

        query = "SELECT * FROM logging WHERE guild_id = $1 AND channel_id = $2"
        record = cast(
            Optional[Record],
            await self.bot.db.fetchrow(query, ctx.guild.id, channel.id),
        )
        if not record:
            return await ctx.warn(f"{channel.mention} is not set up to log any events")

        events = [f"`{event}`" for event in record["events"]]
        return await ctx.respond(
            f"{channel.mention} is receiving {plural(len(events)):event}\n>>> "
            + "\n".join(", ".join(chunk) for chunk in as_chunks(events, 3))
        )

    @logging.command(name="list")
    @has_permissions(manage_guild=True)
    async def logging_list(self, ctx: Context) -> Message:
        """View all channels set up for logging."""

        query = "SELECT * FROM logging WHERE guild_id = $1"
        records = cast(list[Record], await self.bot.db.fetch(query, ctx.guild.id))
        channels = [
            f"{channel.mention} is receiving {plural(len(record['events'])):event}"
            for record in records
            if (channel := ctx.guild.get_channel_or_thread(record["channel_id"]))
        ]
        if not channels:
            return await ctx.warn("No channels are set up to log events")

        embed = Embed(title="Logging Channels")
        paginator = Paginator(ctx, channels, embed)
        return await paginator.start()

    @Cog.listener("on_member_join")
    async def log_member_join(self, member: Member) -> None:
        """Log when a member joins the guild."""

        embed = Embed(title="Member Joined")
        if member.created_at > utcnow() - timedelta(days=1):
            embed.description = "**⚠ Account created less than a day ago**"

        embed.add_field(
            name="Account Created",
            value=f"{format_dt(member.created_at, 'F')} ({format_dt(member.created_at, 'R')})",
            inline=False,
        )
        if member.joined_at:
            embed.add_field(
                name="Joined Server",
                value=f"{format_dt(member.joined_at, 'F')} ({format_dt(member.joined_at, 'R')})",
                inline=False,
            )

        await log(LogType.MEMBER, member.guild, embed, user=member)

    @Cog.listener("on_member_remove")
    async def log_member_remove(self, member: Member) -> None:
        """Log when a member leaves the guild."""

        if not member.joined_at:
            return

        embed = Embed(title="Member Left")
        embed.add_field(
            name="Joined Server",
            value=f"{format_dt(member.joined_at, 'F')} ({format_dt(member.joined_at, 'R')})",
            inline=False,
        )

        await log(LogType.MEMBER, member.guild, embed, user=member)

    @Cog.listener("on_member_update")
    async def log_member_update(self, before: Member, after: Member) -> None:
        """Log when a member's details are updated."""

        changes: list[tuple[str, str]] = []
        if before.nick != after.nick:
            changes.append(("Nickname", f"{before.nick} → {after.nick}"))

        if before.guild_avatar != after.guild_avatar:
            changes.append(("Server Avatar", "Server avatar changed"))

        if not changes:
            return

        embed = Embed(title="Member Updated")
        for name, value in changes:
            embed.add_field(name=name, value=value, inline=False)

        await log(LogType.MEMBER, after.guild, embed, user=after)

    @Cog.listener("on_message_delete")
    async def log_message_delete(self, message: Message) -> None:
        """Log when a message is deleted."""

        if (
            not message.guild
            or message.author.bot
            or isinstance(
                message.channel,
                (
                    GroupChannel,
                    DMChannel,
                    PartialMessageable,
                ),
            )
        ):
            return

        embed = Embed(
            title="Message Deleted",
            description=f"Message from {message.author.mention} deleted in {message.channel.mention}",
        )
        if message.system_content:
            embed.add_field(
                name="Message Content",
                value=message.system_content[:1024],
                inline=False,
            )

        if message.stickers:
            embed.set_image(url=message.stickers[0].url)

        for embed_ in message.embeds:
            if embed_.image:
                embed.set_image(url=embed_.image.url)
                break

        files: list[File] = []
        for attachment in message.attachments:
            with suppress(HTTPException):
                file = await attachment.to_file(
                    description=f"Attachment from {message.author}'s message",
                    spoiler=attachment.is_spoiler(),
                )
                files.append(file)

        if not embed.fields and not files:
            return

        await log(
            LogType.MESSAGE,
            message.guild,
            embed,
            user=message.author,
            files=files,
        )

    @Cog.listener("on_message_edit")
    async def log_message_edit(self, before: Message, after: Message) -> None:
        """Log when a message is edited."""

        if (
            not after.guild
            or after.author.bot
            or isinstance(
                after.channel,
                (
                    GroupChannel,
                    DMChannel,
                    PartialMessageable,
                ),
            )
        ):
            return

        embed = Embed(title="Message Edited")
        embed.description = ""
        if before.system_content != after.system_content:
            embed.description = f"Message from {before.author.mention} edited"
            for key, value in (
                ("Before", before.system_content),
                ("After", after.system_content),
            ):
                embed.add_field(name=key, value=value[:1024], inline=False)

        elif before.attachments and not after.attachments:
            embed.description = (
                f"Attachment removed from {before.author.mention}'s message"
            )

        elif before.embeds and not after.embeds:
            for embed_ in before.embeds:
                if embed_.image:
                    embed.set_image(url=embed_.image.url)
                    break

        if not embed.description and not embed.image:
            return

        embed.description += (
            f"\n> [Jump to the message]({after.jump_url}) in {after.channel.mention}"
        )
        files: list[File] = []
        for attachment in before.attachments:
            if attachment in after.attachments:
                continue

            with suppress(HTTPException):
                file = await attachment.to_file(
                    description=f"Attachment from {before.author}'s message",
                    spoiler=attachment.is_spoiler(),
                )
                files.append(file)

        await log(
            LogType.MESSAGE,
            after.guild,
            embed,
            user=after.author,
            files=files,
        )

    @Cog.listener("on_bulk_message_delete")
    async def log_bulk_message_delete(self, messages: list[Message]) -> None:
        """Log when messages are bulk deleted."""

        if not messages:
            return

        guild = messages[0].guild
        channel = messages[0].channel
        if not guild or isinstance(
            channel,
            (
                GroupChannel,
                DMChannel,
                PartialMessageable,
            ),
        ):
            return

        embed = Embed(title="Messages Deleted")
        embed.description = (
            f"{plural(len(messages)):message} were deleted in {channel.mention}"
        )
        if messages[0].created_at.minute != messages[-1].created_at.minute:
            embed.description += f"\n> They were sent between {format_dt(messages[0].created_at, 't')} and {format_dt(messages[-1].created_at, 't')}"

        members = list({message.author for message in messages})
        if len(members) > 1:
            embed.add_field(
                name=format(plural(len(members)), "member"),
                value="\n".join(
                    [f"> {member.mention} [`{member.id}`]" for member in members[:10]],
                )
                + (
                    f"\n... and {plural(len(members) - 10):more}"
                    if len(members) > 10
                    else ""
                ),
                inline=False,
            )

        buffer = BytesIO()
        for message in messages:
            buffer.write(
                f"[{message.created_at:%d/%m/%Y - %H:%M}] {message.author} ({message.author.id}): {message.system_content or "No content available"}\n".encode(),
            )

        buffer.seek(0)
        await log(
            LogType.MESSAGE,
            guild,
            embed,
            user=members[0] if len(members) == 1 else None,
            files=[File(buffer, filename="messages.txt")],
        )

    @Cog.listener("on_audit_log_entry_role_create")
    async def log_role_creation(self, entry: AuditLogEntry) -> None:
        """Log when a role is created."""

        role = cast(Role, entry.target)
        embed = Embed(title="Role Created")
        embed.description = f"Role {role.mention} created"
        if entry.user:
            embed.description += f" by {entry.user.mention}"

        if role.is_integration():
            embed.description += " (integration)"

        for key, value in (
            ("Name", role.name),
            ("Color", f"`{role.color}`"),
            ("ID", f"`{role.id}`"),
        ):
            embed.add_field(name=key, value=value)

        await log(LogType.ROLE, entry.guild, embed, user=entry.user)

    @Cog.listener("on_audit_log_entry_role_delete")
    async def log_role_deletion(self, entry: AuditLogEntry) -> None:
        """Log when a role is deleted."""

        if isinstance(entry.target, Object):
            return

        role = cast(Role, entry.target)
        embed = Embed(title="Role Deleted")
        embed.description = f"Role {role.name} deleted"
        if entry.user:
            embed.description += f" by {entry.user.mention}"

        await log(LogType.ROLE, entry.guild, embed, user=entry.user)

    @Cog.listener("on_audit_log_entry_role_update")
    async def log_role_update(self, entry: AuditLogEntry) -> None:
        """Log when a role is updated."""

        role = cast(Role, entry.target)
        embed = Embed(title="Role Updated")
        embed.description = f"Role {role.mention} updated"
        if entry.user:
            embed.description += f" by {entry.user.mention}"

        if role.color != Color.default():
            embed.color = role.color

        if isinstance(role.display_icon, Asset):
            embed.set_thumbnail(url=role.display_icon)

        changes: list[tuple[str, str]] = []
        if before := entry.before:
            if getattr(before, "name", role.name) != role.name:
                changes.append(("Name", f"{before.name} → {role.name}"))

            if getattr(before, "color", role.color) != role.color:
                changes.append(("Color", f"`{before.color}` → `{role.color}`"))

            if getattr(before, "hoist", role.hoist) != role.hoist:
                changes.append(
                    (
                        "Hoisted",
                        f"The role is {'now' if role.hoist else 'no longer'} hoisted",
                    )
                )

            if getattr(before, "mentionable", role.mentionable) != role.mentionable:
                changes.append(
                    (
                        "Mentionable",
                        f"The role is {'now' if role.mentionable else 'no longer'} mentionable",
                    )
                )

            if getattr(before, "position", role.position) != role.position:
                changes.append(("Position", f"{before.position} → {role.position}"))

            if getattr(before, "permissions", role.permissions) != role.permissions:
                changes.append(
                    (
                        "Permissions",
                        "\n".join(
                            [
                                f"> `{'✅' if status else '❌'}`"
                                f" **{permission.replace('_', ' ').title()}**"
                                for permission, status in role.permissions
                                if status != getattr(before.permissions, permission)
                            ]
                        ),
                    )
                )

        if not changes:
            return

        for name, value in changes:
            embed.add_field(name=name, value=value, inline=False)

        if not embed.fields:
            return

        await log(LogType.ROLE, entry.guild, embed, user=entry.user)

    @Cog.listener("on_audit_log_entry_member_role_update")
    async def log_member_role_update(self, entry: AuditLogEntry) -> None:
        """Log when a member's roles are updated."""

        member = cast(Member, entry.target)
        embed = Embed(title="Member Roles Updated")
        embed.description = f"Roles for {member.mention} were updated"
        if entry.user and entry.user != member:
            embed.description += f" by {entry.user.mention}"

        granted = [
            role.mention for role in entry.after.roles if role not in entry.before.roles
        ]
        removed = [
            role.mention for role in entry.before.roles if role not in entry.after.roles
        ]
        if granted:
            embed.add_field(
                name="Roles Granted",
                value=", ".join(granted),
                inline=False,
            )

        if removed:
            embed.add_field(
                name="Roles Removed",
                value=", ".join(removed),
                inline=False,
            )

        await log(LogType.MEMBER, entry.guild, embed, user=member)

    @Cog.listener("on_audit_log_entry_channel_create")
    async def log_channel_creation(self, entry: AuditLogEntry) -> None:
        """Log when a channel is created."""

        channel = cast(TextChannel, entry.target)
        embed = Embed(title="Channel Created")
        embed.description = (
            f"{channel.type.name.title()} Channel {channel.mention} created"
        )
        if entry.user:
            embed.description += f" by {entry.user.mention}"

        for key, value in (
            ("Name", channel.name),
            ("ID", f"`{channel.id}`"),
        ):
            embed.add_field(name=key, value=value)

        await log(LogType.CHANNEL, entry.guild, embed, user=entry.user)

    @Cog.listener("on_audit_log_entry_channel_delete")
    async def log_channel_deletion(self, entry: AuditLogEntry) -> None:
        """Log when a channel is deleted."""

        entry.target = cast(Object, entry.target)
        channel = cast(GuildChannel, entry.before)

        embed = Embed(title="Channel Deleted")
        embed.description = f"{channel.type.name.replace('_', ' ').title()} Channel **{channel.name}** deleted"
        if entry.user:
            embed.description += f" by {entry.user.mention}"

        embed.add_field(
            name="Channel Created",
            value=f"{format_dt(entry.target.created_at, 'F')} ({format_dt(entry.target.created_at, 'R')})",
        )

        await log(LogType.CHANNEL, entry.guild, embed, user=entry.user)

    @Cog.listener("on_audit_log_entry_invite_create")
    async def log_invite_creation(self, entry: AuditLogEntry) -> None:
        """Log when an invite is created."""

        invite = cast(Invite, entry.target)
        embed = Embed(title="Invite Created")
        embed.description = f"{invite.temporary and 'Temporary' or ''} Invite [`{invite.code}`]({invite.url}) created"
        if entry.user:
            embed.description += f" by {entry.user.mention}"

        if isinstance(invite.channel, GuildChannel):
            embed.add_field(name="Channel", value=invite.channel.mention)

        if invite.max_uses:
            embed.add_field(name="Max Uses", value=f"`{invite.max_uses}`")

        if invite.max_age:
            embed.add_field(name="Max Age", value=format_timespan(invite.max_age))

        await log(LogType.INVITE, entry.guild, embed, user=entry.user)

    @Cog.listener("on_audit_log_entry_invite_delete")
    async def log_invite_deletion(self, entry: AuditLogEntry) -> None:
        """Log when an invite is deleted."""

        invite = cast(Invite, entry.target)
        embed = Embed(title="Invite Deleted")
        embed.description = f"Invite [`{invite.code}`]({invite.url}) deleted"
        if entry.user:
            embed.description += f" by {entry.user.mention}"

        if invite.uses:
            embed.add_field(
                name="Uses",
                value=f"`{invite.uses}`/`{invite.max_uses or '∞'}`",
            )

        if invite.inviter and invite.inviter != entry.user:
            embed.add_field(name="Inviter", value=invite.inviter.mention)

        await log(LogType.INVITE, entry.guild, embed, user=entry.user)

    @Cog.listener("on_audit_log_entry_emoji_create")
    async def log_emoji_creation(self, entry: AuditLogEntry) -> None:
        """Log when an emoji is created."""

        emoji = cast(Emoji, entry.target)
        embed = Embed(title="Emoji Created")
        embed.description = f"Emoji {emoji} created"
        if entry.user:
            embed.description += f" by {entry.user.mention}"

        embed.set_thumbnail(url=emoji.url)
        for key, value in (
            ("Name", emoji.name),
            ("ID", f"`{emoji.id}`"),
        ):
            embed.add_field(name=key, value=value)

        await log(LogType.EMOJI, entry.guild, embed, user=entry.user)

    @Cog.listener("on_audit_log_entry_emoji_update")
    async def log_emoji_update(self, entry: AuditLogEntry) -> None:
        """Log when an emoji is updated."""

        emoji = cast(Emoji, entry.target)
        embed = Embed(title="Emoji Updated")
        embed.description = f"Emoji {emoji} updated"
        if entry.user:
            embed.description += f" by {entry.user.mention}"

        if getattr(entry.before, "name", emoji.name) != emoji.name:
            embed.add_field(name="Name", value=f"{entry.before.name} → {emoji.name}")

        if getattr(entry.before, "roles", emoji.roles) != emoji.roles:
            embed.add_field(
                name="Required Roles",
                value=human_join(
                    [role.mention for role in emoji.roles],
                    final="and",
                ),
            )

        if not embed.fields:
            return

        await log(LogType.EMOJI, entry.guild, embed, user=entry.user)

    @Cog.listener("on_audit_log_entry_sticker_create")
    async def log_sticker_creation(self, entry: AuditLogEntry) -> None:
        """Log when a sticker is created."""

        sticker = cast(GuildSticker, entry.target)
        embed = Embed(title="Sticker Created")
        embed.description = f"Sticker {sticker} created"
        if entry.user:
            embed.description += f" by {entry.user.mention}"

        embed.set_thumbnail(url=sticker.url)
        for key, value in (
            ("Name", sticker.name),
            ("ID", f"`{sticker.id}`"),
        ):
            embed.add_field(name=key, value=value)

        await log(LogType.STICKER, entry.guild, embed, user=entry.user)

    @Cog.listener("on_audit_log_entry_sticker_update")
    async def log_sticker_update(self, entry: AuditLogEntry) -> None:
        """Log when a sticker is updated."""

        sticker = cast(GuildSticker, entry.target)
        embed = Embed(title="Sticker Updated")
        embed.description = f"Sticker {sticker} updated"
        if entry.user:
            embed.description += f" by {entry.user.mention}"

        if getattr(entry.before, "name", sticker.name) != sticker.name:
            embed.add_field(name="Name", value=f"{entry.before.name} → {sticker.name}")

        if (
            getattr(entry.before, "description", sticker.description)
            != sticker.description
        ):
            embed.add_field(
                name="Description",
                value=f"{entry.before.description} → {sticker.description or 'None'}",
            )

        if getattr(entry.before, "emoji", sticker.emoji) != sticker.emoji:
            embed.add_field(
                name="Emoji",
                value=f"{entry.before.emoji} → {sticker.emoji}",
            )

        if not embed.fields:
            return

        await log(LogType.STICKER, entry.guild, embed, user=entry.user)

    @Cog.listener("on_voice_state_update")
    async def log_voice_state_update(
        self,
        member: Member,
        before: VoiceState,
        after: VoiceState,
    ) -> None:
        """Log when a member's voice state is updated."""

        embed = Embed(title="Voice State Updated")
        if before.channel == after.channel and after.channel:
            if before.self_mute != after.self_mute:
                embed.description = f"{member.mention} {'muted' if after.self_mute else 'unmuted'} themselves"

            elif before.self_deaf != after.self_deaf:
                embed.description = f"{member.mention} {'deafened' if after.self_deaf else 'undeafened'} themselves"

            elif before.self_stream != after.self_stream:
                embed.description = f"{member.mention} {'started' if after.self_stream else 'stopped'} streaming"

            elif before.self_video != after.self_video:
                embed.description = f"{member.mention} {'started' if after.self_video else 'stopped'} video"

            elif before.mute != after.mute:
                embed.description = f"{member.mention} was {'muted' if after.mute else 'unmuted'} by an admin"

            elif before.deaf != after.deaf:
                embed.description = f"{member.mention} was {'deafened' if after.deaf else 'undeafened'} by an admin"

            elif before.suppress != after.suppress:
                embed.description = f"{member.mention} was {'suppressed' if after.suppress else 'unsuppressed'} by an admin"

        elif not before.channel and after.channel:
            embed.description = f"{member.mention} joined **{after.channel}**"

        elif before.channel and not after.channel:
            embed.description = f"{member.mention} left **{before.channel}**"

        elif before.channel and after.channel:
            embed.description = f"{member.mention} moved from **{before.channel}** to **{after.channel}**"

            embed.add_field(
                name="**Previously occupied channel**",
                value=f"{before.channel.mention} ({before.channel})",
                inline=False,
            )

        if after.channel:
            embed.add_field(
                name="**Voice Channel**",
                value=f"{after.channel.mention} ({after.channel})",
                inline=False,
            )

        if not embed.description or not embed.fields:
            return

        await log(LogType.VOICE, member.guild, embed, user=member)
