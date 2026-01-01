from __future__ import annotations

import asyncio
from contextlib import suppress
from logging import getLogger
from typing import TYPE_CHECKING, List, Literal, Optional, TypedDict, cast

from discord import (
    CategoryChannel,
    HTTPException,
    Member,
    Message,
    RateLimited,
    Role,
    VoiceChannel,
    VoiceState,
)
from discord.ext.commands import (
    BucketType,
    Cog,
    CommandError,
    Range,
    cooldown,
    group,
    has_permissions,
)
from humanfriendly import format_timespan

from bot.core import Context as OriginalContext
from bot.core import Juno
from bot.shared import script
from bot.shared.formatter import plural

if TYPE_CHECKING:
    from discord.guild import VocalGuildChannel

logger = getLogger("bot.voicemaster")


class MemberVoice(VoiceState):
    channel: VoiceChannel


class MemberInVoice(Member):
    voice: MemberVoice


class Context(OriginalContext):
    author: MemberInVoice


class Record(TypedDict):
    guild_id: int
    channel_id: int
    owner_id: int


class ConfigRecord(TypedDict):
    guild_id: int
    channel_id: int
    category_id: int
    bitrate: Optional[int]
    template: Optional[str]
    status_template: Optional[str]


def is_empty(channel: VocalGuildChannel) -> bool:
    members = filter(lambda member: not member.bot, channel.members)
    return not any(members)


async def is_in_voice(ctx: Context) -> bool:
    """Check if the invoker is in a voice channel."""

    configuration_commands = (
        "voicemaster setup",
        "voicemaster reset",
        "voicemaster default",
    )
    if not ctx.command.qualified_name.startswith("voicemaster"):
        return True

    elif not ctx.command.parent or ctx.command.qualified_name.startswith(
        configuration_commands
    ):
        return True

    elif not ctx.author.voice or not ctx.author.voice.channel:
        raise CommandError("You are not in a voice channel")

    query = "SELECT owner_id FROM voicemaster.channel WHERE channel_id = $1"
    owner_id = cast(
        Optional[int],
        await ctx.bot.db.fetchval(query, ctx.author.voice.channel.id),
    )
    if not owner_id:
        raise CommandError("This is not a VoiceMaster channel")

    elif ctx.command.name == "claim":
        if ctx.author.id == owner_id:
            raise CommandError("You are already the owner of this channel")

        elif owner_id in {member.id for member in ctx.author.voice.channel.members}:
            raise CommandError("The owner is still in the channel")

        return True

    elif ctx.author.id != owner_id:
        raise CommandError("You are not the owner of this channel")

    return True


class VoiceMaster(Cog):
    def __init__(self, bot: Juno):
        self.bot = bot

    async def cog_load(self) -> None:
        self.bot.add_check(is_in_voice)
        self.bot.loop.create_task(self.cleanup_unoccupied_channels())
        return await super().cog_load()

    async def cog_unload(self) -> None:
        self.bot.remove_check(is_in_voice)
        return await super().cog_unload()

    async def cleanup_unoccupied_channels(self) -> None:
        """Delete VoiceMaster channels that aren't in use."""

        query = "SELECT channel_id FROM voicemaster.channel"
        records = cast(list[Record], await self.bot.db.fetch(query))
        queued: List[int] = []

        for record in records:
            channel = cast(
                Optional[VoiceChannel],
                self.bot.get_channel(record["channel_id"]),
            )
            if channel and is_empty(channel):
                queued.append(record["channel_id"])
                if channel:
                    with suppress(HTTPException):
                        await channel.delete()

        if queued:
            query = """
            DELETE FROM voicemaster.channel
            WHERE channel_id = ANY($1::BIGINT[])
            """
            await self.bot.db.execute(query, queued)
            logger.info(f"Deleted {plural(queued):channel} which were unoccupied")

    @Cog.listener("on_voice_state_update")
    async def create_voice_channel(
        self,
        member: Member,
        before: VoiceState,
        after: VoiceState,
    ) -> None:
        """Create a VoiceMaster channel for the member."""

        guild = member.guild
        after_channel = after.channel
        if member.bot or not after_channel:
            return

        elif before.channel == after_channel:
            return

        elif not guild.me.guild_permissions.administrator:
            return

        query = "SELECT * FROM voicemaster.config WHERE guild_id = $1"
        config = cast(
            Optional[ConfigRecord],
            await self.bot.db.fetchrow(query, guild.id),
        )
        if not config or config["channel_id"] != after_channel.id:
            return

        ratelimited, guild_ratelimited = (
            await self.bot.redis.ratelimited(
                f"voicemaster:{member.id}",
                limit=1,
                timespan=10,
            ),
            await self.bot.redis.ratelimited(
                f"voicemaster:{guild.id}",
                limit=10,
                timespan=40,
            ),
        )
        if ratelimited:
            await member.move_to(None)
            return

        elif guild_ratelimited:
            return

        category: Optional[CategoryChannel] = None
        if config["category_id"] != 0:
            category = cast(
                Optional[CategoryChannel],
                guild.get_channel(config["category_id"]) or after_channel.category,
            )

        name = config["template"] or f"{member.display_name}'s channel"
        bitrate = min(
            config["bitrate"] or guild.bitrate_limit,
            guild.bitrate_limit,
        )

        try:
            channel = await guild.create_voice_channel(
                name=script.parse(name, guild=guild, member=member)[:100],
                category=category,
                bitrate=int(bitrate),
                reason=f"VoiceMaster channel for {member}",
            )
            await channel.set_permissions(
                channel.guild.default_role,
                speak=True,
                connect=True,
                view_channel=True,
                read_messages=True,
            )
        except HTTPException as exc:
            logger.error(
                f"Failed to create channel for {member} in {guild}", exc_info=exc
            )
            return
        else:
            logger.info(f"Created voice channel for {member} in {guild}")

        try:
            await member.move_to(channel)
        except HTTPException:
            with suppress(HTTPException):
                await channel.delete()
            
            return

        query = "INSERT INTO voicemaster.channel VALUES ($1, $2, $3)"
        await self.bot.db.execute(query, guild.id, channel.id, member.id)
        if config["status_template"]:
            with suppress(HTTPException):
                await channel.edit(
                    status=script.parse(
                        config["status_template"],
                        guild=guild,
                        member=member,
                    )[:500]
                )

    @Cog.listener("on_voice_state_update")
    async def disconnect_locked_channel(
        self,
        member: Member,
        before: VoiceState,
        after: VoiceState,
    ) -> None:
        """Disconnect members from locked VoiceMaster channels."""

        guild = member.guild
        if member.bot or not after.channel:
            return

        elif before.channel == after.channel:
            return

        elif not guild.me.guild_permissions.administrator:
            return

        channel = after.channel
        if channel.overwrites_for(guild.default_role).connect is not False:
            return

        elif channel.overwrites_for(member).connect is not False:
            return

        query = "SELECT 1 FROM voicemaster.channel WHERE channel_id = $1"
        is_voicemaster = cast(
            Optional[bool],
            await self.bot.db.fetchval(query, channel.id),
        )
        if not is_voicemaster:
            return

        with suppress(HTTPException):
            await member.move_to(None, reason="VoiceMaster channel is locked")

    @Cog.listener("on_voice_state_update")
    async def delete_voice_channel(
        self,
        member: Member,
        before: VoiceState,
        after: VoiceState,
    ) -> None:
        """Delete VoiceMaster channels that are unoccupied."""

        if not before.channel or before.channel == after.channel:
            return

        elif not is_empty(before.channel):
            return

        query = "DELETE FROM voicemaster.channel WHERE channel_id = $1"
        result = await self.bot.db.execute(query, before.channel.id)
        if result == "DELETE 0":
            return

        with suppress(HTTPException):
            await before.channel.delete()

    @group(aliases=("voice", "vm", "vc"), invoke_without_command=True)
    async def voicemaster(self, ctx: Context) -> Message:
        """Configure temporary personal voice channels."""

        return await ctx.send_help(ctx.command)

    @voicemaster.command(name="setup")
    @cooldown(1, 30, BucketType.guild)
    @has_permissions(manage_channels=True)
    async def voicemaster_setup(self, ctx: Context) -> Message:
        """Setup the VoiceMaster creation channel."""

        query = "SELECT channel_id FROM voicemaster.config WHERE guild_id = $1"
        channel_id = cast(
            Optional[int],
            await self.bot.db.fetchval(query, ctx.guild.id),
        )
        if channel_id and (channel := ctx.guild.get_channel(channel_id)):
            return await ctx.warn(
                f"The VoiceMaster channel is already set to {channel.mention}",
                f"Use the `{ctx.clean_prefix}voicemaster reset` command to remove it",
            )

        query = """
        INSERT INTO voicemaster.config (guild_id, channel_id, category_id)
        VALUES ($1, $2, $3) ON CONFLICT (guild_id)
        DO UPDATE SET
            channel_id = EXCLUDED.channel_id,
            category_id = EXCLUDED.category_id
        """
        category = await ctx.guild.create_category("Voice Channels")
        channel = await category.create_voice_channel("Join to Create")
        await self.bot.db.execute(query, ctx.guild.id, channel.id, category.id)
        
        return await ctx.approve(
            "Successfully setup the VoiceMaster integration",
            f"Join {channel.mention} to create your own channel",
        )

    @voicemaster.command(name="reset")
    @has_permissions(manage_channels=True)
    async def voicemaster_reset(self, ctx: Context) -> Message:
        """Reset the VoiceMaster integration."""

        query = "DELETE FROM voicemaster.config WHERE guild_id = $1 RETURNING *"
        record = cast(
            Optional[ConfigRecord],
            await self.bot.db.fetchrow(query, ctx.guild.id),
        )
        if not record:
            return await ctx.warn("The VoiceMaster integration is not setup")

        with suppress(HTTPException):
            for channel_id in {record["channel_id"], record["category_id"]}:
                channel = ctx.guild.get_channel(channel_id)
                if channel:
                    await channel.delete()

        return await ctx.approve("Successfully reset the VoiceMaster integration")

    @voicemaster.group(name="default", aliases=("set",), invoke_without_command=True)
    @has_permissions(manage_channels=True)
    async def voicemaster_default(self, ctx: Context) -> Message:
        """Configure the default settings for VoiceMaster channels."""

        return await ctx.send_help(ctx.command)

    @voicemaster_default.command(name="category")
    @has_permissions(manage_channels=True)
    async def voicemaster_default_category(
        self,
        ctx: Context,
        category: CategoryChannel | Literal["none"],
    ) -> Message:
        """Set the category for VoiceMaster channels."""

        query = """
        UPDATE voicemaster.config
        SET category_id = $2
        WHERE guild_id = $1
        """
        result = await self.bot.db.execute(
            query,
            ctx.guild.id,
            category.id if category != "none" else 0,
        )
        if result == "UPDATE 0":
            return await ctx.warn("The VoiceMaster integration is not setup")

        return await ctx.approve(
            f"Now placing voice channels under **{category}**"
            if isinstance(category, CategoryChannel)
            else "No longer placing voice channels under a category"
        )

    @voicemaster_default.group(name="name", invoke_without_command=True)
    @has_permissions(manage_channels=True)
    async def voicemaster_default_name(
        self,
        ctx: Context,
        *,
        template: Range[str, 1, 100],
    ) -> Message:
        """Set the name template for VoiceMaster channels."""

        query = """
        UPDATE voicemaster.config
        SET template = $2
        WHERE guild_id = $1
        """
        result = await self.bot.db.execute(query, ctx.guild.id, template)
        if result == "UPDATE 0":
            return await ctx.warn("The VoiceMaster integration is not setup")

        parsed = script.parse(template, guild=ctx.guild, member=ctx.author)
        return await ctx.approve(
            "Now using a template for voice channel names",
            f"It will be displayed as **{parsed}**",
        )

    @voicemaster_default_name.command(
        name="remove",
        aliases=("delete", "reset", "del", "rm"),
        hidden=True,
    )
    @has_permissions(manage_channels=True)
    async def voicemaster_default_name_remove(self, ctx: Context) -> Message:
        """Remove the name template for VoiceMaster channels."""

        query = """
        UPDATE voicemaster.config
        SET template = NULL
        WHERE guild_id = $1
        """
        result = await self.bot.db.execute(query, ctx.guild.id)
        if result == "UPDATE 0":
            return await ctx.warn("The VoiceMaster integration is not setup")

        return await ctx.approve("No longer using a template for voice channel names")

    @voicemaster_default.group(name="status", invoke_without_command=True)
    @has_permissions(manage_channels=True)
    async def voicemaster_default_status(
        self,
        ctx: Context,
        *,
        template: Range[str, 1, 500],
    ) -> Message:
        """Set the status template for VoiceMaster channels."""

        query = """
        UPDATE voicemaster.config
        SET status_template = $2
        WHERE guild_id = $1
        """
        result = await self.bot.db.execute(query, ctx.guild.id, template)
        if result == "UPDATE 0":
            return await ctx.warn("The VoiceMaster integration is not setup")

        parsed = script.parse(template, guild=ctx.guild, member=ctx.author)
        return await ctx.approve(
            "Now using a template for voice channel statuses",
            f"It will be displayed as **{parsed}**",
        )

    @voicemaster_default_status.command(
        name="remove",
        aliases=("delete", "reset", "del", "rm"),
        hidden=True,
    )
    @has_permissions(manage_channels=True)
    async def voicemaster_default_status_remove(self, ctx: Context) -> Message:
        """Remove the status template for VoiceMaster channels."""

        query = """
        UPDATE voicemaster.config
        SET status_template = NULL
        WHERE guild_id = $1
        """
        result = await self.bot.db.execute(query, ctx.guild.id)
        if result == "UPDATE 0":
            return await ctx.warn("The VoiceMaster integration is not setup")

        return await ctx.approve(
            "No longer using a template for voice channel statuses"
        )

    @voicemaster.command(name="claim")
    async def voicemaster_claim(self, ctx: Context) -> Message:
        """Claim an unoccupied voice channel."""

        channel = ctx.author.voice.channel
        if (
            channel.name.endswith("'s channel")
            and ctx.author.display_name not in channel.name
        ):

            async def callback() -> None:
                with suppress(HTTPException):
                    await channel.edit(name=f"{ctx.author.display_name}'s channel")

            self.bot.loop.create_task(callback())

        query = "UPDATE voicemaster.channel SET owner_id = $2 WHERE channel_id = $1"
        await self.bot.db.execute(query, channel.id, ctx.author.id)
        return await ctx.approve(f"You're now the owner of {channel.mention}")

    @voicemaster.command(name="transfer", aliases=("give",))
    async def voicemaster_transfer(self, ctx: Context, *, member: Member) -> Message:
        """Transfer ownership of your voice channel."""

        channel = ctx.author.voice.channel
        if member == ctx.author or member.bot:
            return await ctx.warn("You can't transfer ownership to yourself or a bot")

        elif member not in channel.members:
            return await ctx.warn("That member is not in your voice channel")

        if (
            channel.name.endswith("'s channel")
            and ctx.author.display_name not in channel.name
        ):

            async def callback() -> None:
                with suppress(HTTPException):
                    await channel.edit(name=f"{ctx.author.display_name}'s channel")

            self.bot.loop.create_task(callback())

        query = "UPDATE voicemaster.channel SET owner_id = $2 WHERE channel_id = $1"
        await self.bot.db.execute(query, channel.id, member.id)
        return await ctx.approve(f"You've transferred ownership to {member.mention}")

    @voicemaster.command(name="lock")
    async def voicemaster_lock(self, ctx: Context) -> Optional[Message]:
        """Prevent members from joining your voice channel."""

        channel = ctx.author.voice.channel
        if channel.overwrites_for(ctx.guild.default_role).connect is False:
            return await ctx.warn("Your voice channel is already locked")

        await channel.set_permissions(
            ctx.guild.default_role,
            connect=False,
            reason=f"{ctx.author} locked the channel",
        )
        with suppress(HTTPException):
            await asyncio.gather(
                *[
                    channel.set_permissions(member, connect=True)
                    for member in channel.members[:100]
                ]
            )

        return await ctx.add_check()

    @voicemaster.command(name="unlock")
    async def voicemaster_unlock(self, ctx: Context) -> Optional[Message]:
        """Allow members to join your voice channel."""

        channel = ctx.author.voice.channel
        if channel.overwrites_for(ctx.guild.default_role).connect is not False:
            return await ctx.warn("Your voice channel is already unlocked")

        await channel.set_permissions(
            ctx.guild.default_role,
            connect=None,
            reason=f"{ctx.author} unlocked the channel",
        )
        return await ctx.add_check()

    @voicemaster.command(name="hide", aliases=("ghost",))
    async def voicemaster_hide(self, ctx: Context) -> Optional[Message]:
        """Prevent members from seeing your voice channel."""

        channel = ctx.author.voice.channel
        if channel.overwrites_for(ctx.guild.default_role).view_channel is False:
            return await ctx.warn("Your voice channel is already hidden")

        await channel.set_permissions(
            ctx.guild.default_role,
            view_channel=False,
            reason=f"{ctx.author} hid the channel",
        )
        return await ctx.add_check()

    @voicemaster.command(name="reveal", aliases=("show", "unhide"))
    async def voicemaster_reveal(self, ctx: Context) -> Optional[Message]:
        """Allow members to see your voice channel."""

        channel = ctx.author.voice.channel
        if channel.overwrites_for(ctx.guild.default_role).view_channel is not False:
            return await ctx.warn("Your voice channel is already revealed")

        await channel.set_permissions(
            ctx.guild.default_role,
            view_channel=None,
            reason=f"{ctx.author} revealed the channel",
        )
        return await ctx.add_check()

    @voicemaster.command(name="allow", aliases=("permit",))
    async def voicemaster_allow(
        self,
        ctx: Context,
        *,
        target: Member | Role,
    ) -> Message:
        """Allow a member to join your voice channel."""

        channel = ctx.author.voice.channel

        await channel.set_permissions(
            target,
            connect=True,
            view_channel=True,
            reason=f"{ctx.author} allowed {target} to join",
        )
        return await ctx.approve(f"{target.mention} can now join your voice channel")

    @voicemaster.command(name="reject", aliases=("remove", "deny", "kick"))
    async def voicemaster_reject(
        self,
        ctx: Context,
        *,
        target: Member | Role,
    ) -> Message:
        """Prevent a member from joining your voice channel."""

        channel = ctx.author.voice.channel

        await channel.set_permissions(
            target,
            connect=False,
            view_channel=True,
            reason=f"{ctx.author} removed {target} from the channel",
        )
        if isinstance(target, Member) and target in channel.members:
            with suppress(HTTPException):
                await target.move_to(
                    None,
                    reason=f"{ctx.author} removed them from the channel",
                )

        return await ctx.approve(
            f"{target.mention} can no longer join your voice channel"
        )

    @voicemaster.command(name="rename", aliases=("name",))
    async def voicemaster_rename(
        self,
        ctx: Context,
        *,
        name: Range[str, 1, 100],
    ) -> Optional[Message]:
        """Rename your voice channel."""

        channel = ctx.author.voice.channel
        try:
            await channel.edit(
                name=script.parse(name, guild=ctx.guild, member=ctx.author),
                reason=f"{ctx.author} renamed the channel",
            )
        except RateLimited as exc:
            return await ctx.warn(
                "Your voice channel is being rate limited",
                f"Please wait **{format_timespan(exc.retry_after)}** before trying again",
            )

        except HTTPException:
            return await ctx.warn(
                "That name cannot be used",
                "It might contain vulgar language",
            )

        return await ctx.add_check()

    @voicemaster.command(name="limit")
    async def voicemaster_limit(
        self,
        ctx: Context,
        limit: Range[int, 0, 99] | Literal["none"],
    ) -> Message:
        """Limit the number of members in your voice channel."""

        channel = ctx.author.voice.channel
        await channel.edit(
            user_limit=limit if limit != "none" else 0,
            reason=f"{ctx.author} set the user limit to {limit}",
        )
        return await ctx.approve(
            f"Your voice channel now has a limit of {plural(limit):member}"
            if limit and limit != "none"
            else "Your voice channel no longer has a user limit"
        )
