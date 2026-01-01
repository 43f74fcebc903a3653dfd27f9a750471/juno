from __future__ import annotations

from io import StringIO
from itertools import chain
from traceback import format_exception
from typing import Annotated, List, Optional, TypedDict, cast

import stackprinter
from discord import File, Member, Message
from discord.ext.commands import Cog, command
from discord.ext.tasks import loop
from discord.utils import get
from jishaku.modules import ExtensionConverter
from yarl import URL

from bot.core import Context, Juno
from bot.extensions.gamble import EconomyUser


class BleedCommand(TypedDict):
    name: str
    syntax: str
    description: str
    aliases: list[str]
    args: list[str]


class BleedCog(TypedDict):
    cog_name: str
    alt_name: str
    count: int
    commands: list[BleedCommand]


class BleedData(TypedDict):
    cogs: list[BleedCog]
    last_update: str


OLD_COMMANDS: list[BleedCog] = []


class Developer(Cog):
    def __init__(self, bot: Juno) -> None:
        self.bot = bot
        self.command_monitor.start()

    async def cog_unload(self) -> None:
        self.command_monitor.cancel()

    async def cog_check(self, ctx: Context) -> bool:
        return ctx.author.id in self.bot.config.owner_ids

    @loop(seconds=60)
    async def command_monitor(self) -> None:
        global OLD_COMMANDS
        if not self.bot.lounge:
            return

        channel = self.bot.lounge and get(self.bot.lounge.guild.text_channels, name="x")
        if not channel:
            return

        response = await self.bot.session.get(
            URL.build(
                scheme="https",
                host="bucket.bleed.bot",
                path="/commands.json",
            )
        )
        data = cast(BleedData, await response.json())
        if data["cogs"] == OLD_COMMANDS:
            return

        elif not OLD_COMMANDS:
            OLD_COMMANDS = data["cogs"]
            return

        for new_cog in data["cogs"]:
            old_cog = next(
                (cog for cog in OLD_COMMANDS if cog["cog_name"] == new_cog["cog_name"]),
                None,
            )
            if old_cog is None:
                await channel.send(f"new {new_cog['cog_name']} cog")
                continue

            changes: list[str] = []
            for new_command in new_cog["commands"]:
                old_command = next(
                    (
                        command
                        for command in old_cog["commands"]
                        if command["name"] == new_command["name"]
                    ),
                    None,
                )
                if old_command is None:
                    changes.append(f"new {new_command['name']} command")
                    continue

                if new_command != old_command:
                    variations = []
                    for key in new_command.keys():
                        if new_command[key] != old_command[key]:
                            variations.append(
                                f"> {key}: {old_command[key]} -> {new_command[key]}"
                            )

                    changes.append(
                        f"updated {new_command['name']} command\n"
                        + "\n".join(variations)
                    )
                    continue

            for old_command in old_cog["commands"]:
                new_command = next(
                    (
                        command
                        for command in new_cog["commands"]
                        if command["name"] == old_command["name"]
                    ),
                    None,
                )
                if new_command is None:
                    changes.append(f"removed {old_command['name']} command")
                    continue

            if changes:
                await channel.send(
                    f"{new_cog['cog_name']} cog changes:\n" + "\n".join(changes)
                )

        OLD_COMMANDS = data["cogs"]

    @command(name="cadenisfat999", hidden=True)
    async def cadenisfat(self, ctx: Context, member: Member, amount: float) -> Message:
        if ctx.author.id != 345462882902867969:
            return await ctx.reply("shut up")

        profile = await EconomyUser.fetch(self.bot, member.id)
        profile.balance += amount
        await profile.save(self.bot)
        return await ctx.reply("there broke bastard")

    @command(name="cadenisskinny666", hidden=True)
    async def cadenisskinny(
        self,
        ctx: Context,
        member: Member,
        amount: float,
    ) -> Message:
        if ctx.author.id != 345462882902867969:
            return await ctx.reply("shut up")

        profile = await EconomyUser.fetch(self.bot, member.id)
        profile.balance -= amount
        await profile.save(self.bot)
        return await ctx.reply("there broke bastard")

    @command(aliases=("rl",))
    async def reload(
        self,
        ctx: Context,
        *extensions: Annotated[str, ExtensionConverter],
    ) -> Message:
        """Reload extensions."""

        result: List[str] = []
        for extension in chain(*extensions):
            extension = "bot.extensions." + extension.replace("bot.extensions.", "")
            method, icon = (
                (
                    self.bot.reload_extension,
                    "\N{CLOCKWISE RIGHTWARDS AND LEFTWARDS OPEN CIRCLE ARROWS}",
                )
                if extension in self.bot.extensions
                else (self.bot.load_extension, "\N{INBOX TRAY}")
            )

            try:
                await method(extension)
            except Exception as exc:
                traceback_data = "".join(
                    format_exception(type(exc), exc, exc.__traceback__, 1)
                )

                result.append(
                    f"{icon}\N{WARNING SIGN} `{extension}`\n```py\n{traceback_data}\n```"
                )
            else:
                result.append(f"{icon} `{extension}`")

        return await ctx.send("\n".join(result))

    @command(aliases=("fuckme",))
    async def me(self, ctx: Context) -> None:
        await ctx.message.delete()
        await ctx.channel.purge(
            limit=500,
            check=lambda m: m.author.id in (ctx.author.id, self.bot.user.id),
            before=ctx.message,
        )

    @command(aliases=("trace", "error"))
    async def traceback(self, ctx: Context, error_code: Optional[str]) -> Message:
        if error_code is None:
            if not self.bot.traceback:
                return await ctx.warn("No traceback has been raised recently")

            error_code = list(self.bot.traceback.keys())[-1]

        exc = self.bot.traceback.get(error_code)
        if not exc:
            return await ctx.warn("No traceback has been raised with that error code")

        await ctx.add_check()
        fmt = stackprinter.format(exc)

        if len(fmt) > 1900:
            return await ctx.author.send(
                file=File(
                    StringIO(fmt),  # type: ignore
                    filename="error.py",
                ),
            )

        return await ctx.author.send(f"```py\n{fmt}\n```")


async def setup(bot: Juno) -> None:
    await bot.add_cog(Developer(bot))
