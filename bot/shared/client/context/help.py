from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING, Any, List, Mapping, Optional

from discord import Embed, Message
from discord.abc import MISSING
from discord.ext.commands import Cog, Command
from discord.ext.commands.flags import Flag
from discord.ext.commands.flags import FlagsMeta as FlagAnnotation
from discord.ext.commands.help import MinimalHelpCommand
from discord.ext.commands.help import Paginator as HelpPaginator

from bot.shared import Paginator, codeblock
from bot.shared.converters import FlagConverter
from bot.shared.formatter import as_chunks, short_timespan

if TYPE_CHECKING:
    from . import Context


class HelpCommand(MinimalHelpCommand):
    context: Context

    def __init__(self, **options: Any) -> None:
        super().__init__(
            **options,
            verify_checks=False,
            command_attrs=dict(
                hidden=True,
                alaises=("commands", "cmds", "h"),
            ),
            paginator=HelpPaginator(
                suffix=None,
                prefix=None,
                max_size=1000,
            ),
        )

    async def send_pages(self) -> None:
        embeds = [Embed(description=page) for page in self.paginator.pages]

        paginator = Paginator(self.context, embeds)
        await paginator.start()

    async def send_error_message(self, string: str) -> Message:
        return await self.context.warn(string)

    async def send_bot_help(
        self,
        mapping: Mapping[Optional[Cog], List[Command[Any, ..., Any]]],
        /,
    ) -> Message:
        ctx = self.context
        bot = ctx.bot

        embed = Embed()
        embed.set_thumbnail(url=bot.user.display_avatar)
        embed.description = codeblock(
            f"[ {len(set(bot.walk_commands()))} commands ]", "ini"
        )

        cogs = [
            cog
            for cog in bot.cogs.values()
            if cog.qualified_name not in ("Jishaku", "Developer")
        ]
        for cogs in as_chunks(
            sorted(cogs, key=lambda cog: len(cog.qualified_name), reverse=False), 5
        ):
            embed.add_field(
                name="​",
                value="\n".join(
                    [
                        f"[`{cog.qualified_name}`]({bot.config.support.invite})"
                        for cog in cogs
                    ]
                ),
                inline=True,
            )

        MAX_FIELDS_PER_PAGE = 3
        MAX_COMMANDS_PER_FIELD = 6

        embeds = [embed]
        for cog, commands in sorted(
            mapping.items(),
            key=lambda item: len(item[0].qualified_name if item[0] else ""),
            reverse=False,
        ):
            if not cog or cog.qualified_name in ("Jishaku", "Developer"):
                continue

            sorted_commands = sorted(
                commands,
                key=lambda command: len(command.qualified_name),
                reverse=False,
            )

            commands_per_page = MAX_FIELDS_PER_PAGE * MAX_COMMANDS_PER_FIELD
            num_pages = max(
                1, -(-len(sorted_commands) // commands_per_page)
            )  # Ceiling division, minimum 1 page

            for page in range(num_pages):
                embed = Embed()
                embed.set_thumbnail(url=bot.user.display_avatar)

                if num_pages > 1:
                    embed.description = codeblock(
                        f"[ {cog.qualified_name} - Page {page + 1}/{num_pages} ]", "ini"
                    )
                else:
                    embed.description = codeblock(f"[ {cog.qualified_name} ]", "ini")

                start_idx = page * commands_per_page
                end_idx = min((page + 1) * commands_per_page, len(sorted_commands))
                page_commands = sorted_commands[start_idx:end_idx]

                # Distribute commands across fields
                for i in range(0, len(page_commands), MAX_COMMANDS_PER_FIELD):
                    field_commands = page_commands[i : i + MAX_COMMANDS_PER_FIELD]
                    if field_commands:
                        field_value = "\n".join(
                            [
                                f"[`{command.qualified_name}`]({bot.config.support.invite})"
                                for command in field_commands
                            ]
                        )
                        embed.add_field(name="​", value=field_value, inline=True)

                if not page_commands:
                    embed.add_field(
                        name="​", value="No commands available", inline=False
                    )

                embeds.append(embed)

        paginator = Paginator(ctx, embeds)
        return await paginator.start()

    # async def send_bot_help(
    #     self,
    #     mapping: Mapping[Optional[Cog], List[Command[Any, ..., Any]]],
    #     /,
    # ) -> None:
    #     ctx = self.context
    #     bot = ctx.bot

    #     embed = Embed()
    #     embed.set_author(
    #         name=bot.user.display_name,
    #         icon_url=bot.user.display_avatar.url,
    #     )
    #     embed.description = cleandoc(
    #         f"""
    #         All commands can be found via [egirl.software/commands](https://egirl.software/commands)
    #         > Use `{ctx.clean_prefix}help <command>` for more information on a command.
    #     """
    #     )

    #     updates_channel = bot.config.support.updates_channel(bot)
    #     if updates_channel:
    #         suggest_updates = (
    #             ctx.author.guild_permissions.manage_channels
    #             and not await bot.redis.exists(f"updates:{ctx.guild.id}")
    #             and f"\n*Use `{ctx.clean_prefix}updates` to receive updates in a channel.*"
    #             or ""
    #         )
    #         embed.add_field(
    #             name=f"Latest News ({bot.config.version})",
    #             value=cleandoc(
    #                 f"""
    #                 {bot.config.support} [`#{updates_channel.name}`]({updates_channel.jump_url}){suggest_updates}
    #                 >>> {bot.config.version.summary}
    #                 """
    #             ),
    #             inline=False,
    #         )

    #     embed.add_field(
    #         name="Understanding Arguments",
    #         value=cleandoc(
    #             """
    #             *You do not include the brackets in your command.*
    #             >>> `<argument>` denotes a required argument.
    #             `[argument]` denotes an optional argument.
    #             `[argument]...` denotes multiple arguments.
    #             """
    #         ),
    #         inline=False,
    #     )

    #     view = View()
    #     for button in (
    #         Button(
    #             label="Documentation",
    #             url="https://docs.egirl.software",
    #         ),
    #         Button(
    #             label="Support Server",
    #             url="https://discord.gg/juno",
    #         ),
    #     ):
    #         view.add_item(button)

    #     await ctx.send(embed=embed, view=view)

    def get_command_signature(self, command: Command, /) -> str:
        signature = super().get_command_signature(command)
        for default in ("this server", "this channel", "you"):
            signature = signature.replace(f"=<{default}>", "")

        return signature
    
    def add_command_formatting(self, command: Command[Any, ..., Any]) -> None:
        super().add_command_formatting(command)
        if flags := command.extras.get("flags"):
            self._add_flag_formatting(flags)

        for param in command.clean_params.values():
            if isinstance(param.annotation, FlagAnnotation):
                self._add_flag_formatting(param.annotation)  # type: ignore

    def _add_flag_formatting(self, annotation: FlagConverter):
        flags = annotation.get_flags()

        def format_flag(name: str, flag: Flag) -> str:
            default = flag.default
            argument = ""
            if not default not in (MISSING, 0):
                if isinstance(default, timedelta):
                    argument = f"={short_timespan(default)}"
                elif isinstance(default, bool):
                    argument = ""
                else:
                    argument = f"={default}"

            return f"`--{name}{argument}`: {flag.description}"

        optional = [
            format_flag(name, flag)
            for name, flag in flags.items()
            if flag.default is not MISSING
        ]
        required = [
            format_flag(name, flag)
            for name, flag in flags.items()
            if flag.default is MISSING
        ]

        if required:
            self.paginator.add_line("Required Flags:")
            for i, flag in enumerate(required):
                self.paginator.add_line(flag, empty=(i == len(required) - 1))

        if optional:
            self.paginator.add_line("Optional Flags:")
            for flag in optional:
                self.paginator.add_line(flag)
