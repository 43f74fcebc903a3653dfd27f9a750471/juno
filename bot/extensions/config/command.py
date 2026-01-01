from typing import Annotated, List, Optional, cast

from asyncpg import UniqueViolationError
from cashews import cache
from discord import Embed, Message, Role, TextChannel
from discord.ext.commands import Cog
from discord.ext.commands import Command as BaseCommand
from discord.ext.commands import group, has_permissions

from bot.core import Context, Juno
from bot.shared.converters.role import StrictRole
from bot.shared.formatter import plural
from bot.shared.paginator import Paginator


class Command(BaseCommand):
    @classmethod
    async def convert(cls, ctx: Context, argument: str) -> BaseCommand:
        command = ctx.bot.get_command(argument)
        if not command or command.hidden:
            raise ValueError(f"Command `{argument}` not found")

        elif command.qualified_name.startswith("command"):
            raise ValueError("why would u even want to do that ...")

        return command


class CommandManagement(Cog):
    def __init__(self, bot: Juno) -> None:
        self.bot = bot

    async def cog_load(self) -> None:
        self.bot.add_check(self.command_restrictions)
        return await super().cog_load()

    async def cog_unload(self) -> None:
        self.bot.remove_check(self.command_restrictions)
        return await super().cog_unload()

    @cache(
        ttl="30m",
        key=".".join(
            [
                "restriction",
                "{ctx.guild.id}",
                "{ctx.author.id}",
                "{ctx.command.qualified_name}",
            ]
        ),
    )
    async def command_restrictions(self, ctx: Context) -> bool:
        """Check if a command can be invoked."""

        if ctx.author.guild_permissions.administrator:
            return True

        command = ctx.command.qualified_name
        command_parent = ctx.command.full_parent_name

        if isinstance(ctx.channel, TextChannel):
            query = """
            SELECT 1
            FROM commands.disabled
            WHERE channel_id = $1
            AND (
                command = $2
                OR command = $3
            )
            """
            disabled = await self.bot.db.fetchval(
                query,
                ctx.channel.id,
                command,
                command_parent,
            )
            if disabled:
                return False

        query = """
        SELECT 1
        FROM commands.restricted
        WHERE guild_id = $1
        AND NOT role_id = ANY($2::BIGINT[])
        AND (
            command = $3
            OR command = $4
        )
        """
        restricted = await self.bot.db.fetchval(
            query,
            ctx.guild.id,
            [role.id for role in ctx.author.roles],
            command,
            command_parent,
        )
        return not restricted

    @group(aliases=("cmd",), invoke_without_command=True)
    @has_permissions(administrator=True)
    async def command(self, ctx: Context) -> Message:
        """Manage command restrictions.

        If you were to run this command on a command parent like `voicemaster`,
        it would disable or restrict every command under that parent.
        Administrators are exempt from restrictions.
        """

        return await ctx.send_help(ctx.command)

    @command.group(
        name="disable",
        aliases=("off",),
        invoke_without_command=True,
    )
    @has_permissions(administrator=True)
    async def command_disable(
        self,
        ctx: Context,
        channel: Optional[TextChannel],
        *,
        command: Command,
    ) -> Message:
        """Disable a command in a channel or all channels."""

        query = """
        SELECT channel_id
        FROM commands.disabled
        WHERE guild_id = $1
        AND command = $2
        """
        channel_ids = cast(
            List[int],
            [
                record["channel_id"]
                for record in await self.bot.db.fetch(
                    query, ctx.guild.id, command.qualified_name
                )
            ],
        )
        if channel and channel.id in channel_ids:
            return await ctx.warn(
                f"The `{command.qualified_name}` command is already disabled in {channel.mention}"
            )

        elif not channel and all(
            channel.id in channel_ids for channel in ctx.guild.text_channels
        ):
            return await ctx.warn(
                f"The `{command.qualified_name}` command is already disabled in all channels"
            )

        query = """
        INSERT INTO commands.disabled (
            guild_id,
            channel_id,
            command
        ) VALUES ($1, $2, $3)
        ON CONFLICT (guild_id, channel_id, command)
        DO NOTHING
        """
        await self.bot.db.executemany(
            query,
            [
                (ctx.guild.id, channel.id, command.qualified_name)
                for channel in ([channel] if channel else ctx.guild.text_channels)
            ],
        )
        await cache.delete_match(f"restriction.{ctx.guild.id}*")

        if channel:
            return await ctx.approve(
                f"Disabled the `{command.qualified_name}` command in {channel.mention}"
            )

        return await ctx.approve(
            f"Disabled the `{command.qualified_name}` command in {plural(len(ctx.guild.text_channels), md='`'):channel}"
        )

    @command_disable.command(name="view", aliases=("channels",))
    @has_permissions(administrator=True)
    async def command_disable_view(self, ctx: Context, *, command: Command) -> Message:
        """View all channels a command is disabled in."""

        query = """
        SELECT channel_id
        FROM commands.disabled
        WHERE guild_id = $1
        AND command = $2
        """
        channel_ids = cast(
            List[int],
            [
                record["channel_id"]
                for record in await self.bot.db.fetch(
                    query, ctx.guild.id, command.qualified_name
                )
            ],
        )
        channels = [
            f"{channel.mention} [`{channel.id}`]"
            for channel_id in channel_ids
            if (channel := ctx.guild.get_channel(channel_id))
        ]
        if not channels:
            return await ctx.warn(
                f"The `{command.qualified_name}` command is not disabled in any channels"
            )

        embed = Embed(title=f"Disabled Channels for {command.qualified_name}")
        paginator = Paginator(ctx, channels, embed)
        return await paginator.start()

    @command_disable.command(name="list")
    @has_permissions(administrator=True)
    async def command_disable_list(self, ctx: Context) -> Message:
        """View all disabled commands in the server."""

        query = """
        SELECT command, ARRAY_AGG(channel_id) AS channel_ids
        FROM commands.disabled
        WHERE guild_id = $1
        GROUP BY guild_id, command
        """
        records = await self.bot.db.fetch(query, ctx.guild.id)
        commands = [
            f"{record['command']} - {', '.join(channel.mention for channel in channels[:2])}"
            + (f" (+{len(channels) - 2})" if len(channels) > 2 else "")
            for record in records
            if (
                channels := [
                    channel
                    for channel_id in record["channel_ids"]
                    if (channel := ctx.guild.get_channel(channel_id))
                ]
            )
        ]
        if not commands:
            return await ctx.warn("No commands are disabled in this server")

        embed = Embed(title="Disabled Commands")
        paginator = Paginator(ctx, commands, embed)
        return await paginator.start()

    @command.command(name="enable", aliases=("on",))
    @has_permissions(manage_guild=True)
    async def command_enable(
        self,
        ctx: Context,
        channel: Optional[TextChannel],
        *,
        command: Command,
    ) -> Message:
        """Enable a command in a channel or all channels."""

        query = """
        DELETE FROM commands.disabled
        WHERE guild_id = $1
        AND command = $2
        AND channel_id = ANY($3::BIGINT[])
        """
        result = await self.bot.db.execute(
            query,
            ctx.guild.id,
            command.qualified_name,
            [channel.id]
            if channel
            else [channel.id for channel in ctx.guild.text_channels],
        )
        await cache.delete_match(f"restriction.{ctx.guild.id}*")
        if channel and result == "DELETE 0":
            return await ctx.warn(
                f"The `{command.qualified_name}` command is already enabled in {channel.mention}"
            )

        elif not channel and result == "DELETE 0":
            return await ctx.warn(
                f"The `{command.qualified_name}` command reposting is already enabled in all channels"
            )

        if channel:
            return await ctx.approve(
                f"Enabled the `{command.qualified_name}` command in {channel.mention}"
            )

        return await ctx.approve(
            f"Enabled the `{command.qualified_name}` command in {plural(result, md='`'):channel}"
        )

    @command.group(
        name="restrict",
        aliases=("require", "allow", "lock"),
        invoke_without_command=True,
    )
    @has_permissions(administrator=True)
    async def command_restrict(
        self,
        ctx: Context,
        role: Annotated[
            Role,
            StrictRole(
                check_integrated=False,
            ),
        ],
        *,
        command: Command,
    ) -> Message:
        """Restrict access to a command to a role.

        This will remove a restriction if one already exists.
        """

        query = """
        INSERT INTO commands.restricted (
            guild_id,
            role_id,
            command
        ) VALUES ($1, $2, $3)
        """
        await cache.delete_match(f"restriction.{ctx.guild.id}*")
        try:
            await self.bot.db.execute(
                query,
                ctx.guild.id,
                role.id,
                command.qualified_name,
            )
        except UniqueViolationError:
            query = """
            DELETE FROM commands.restricted
            WHERE guild_id = $1
            AND role_id = $2
            AND command = $3
            """
            await self.bot.db.execute(
                query,
                ctx.guild.id,
                role.id,
                command.qualified_name,
            )
            return await ctx.approve(
                f"Removed the restriction on the `{command.qualified_name}` command for {role.mention}"
            )

        return await ctx.approve(
            f"Now allowing {role.mention} to use the `{command.qualified_name}` command"
        )

    @command_restrict.command(name="list")
    @has_permissions(administrator=True)
    async def command_restrict_list(self, ctx: Context) -> Message:
        """View all command restrictions in the server."""

        query = """
        SELECT command, ARRAY_AGG(role_id) AS role_ids
        FROM commands.restricted
        WHERE guild_id = $1
        GROUP BY guild_id, command
        """
        records = await self.bot.db.fetch(query, ctx.guild.id)
        commands = [
            f"{record['command']} - {', '.join(role.mention for role in roles)}"
            for record in records
            if (
                roles := [
                    role
                    for role_id in record["role_ids"]
                    if (role := ctx.guild.get_role(role_id))
                ]
            )
        ]
        if not commands:
            return await ctx.warn("No commands are restricted in this server")

        embed = Embed(title="Restricted Commands")
        paginator = Paginator(ctx, commands, embed)
        return await paginator.start()
