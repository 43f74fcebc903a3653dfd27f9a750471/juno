from importlib import metadata
from platform import python_version
from time import perf_counter
from typing import List, Optional

import discord
import psutil
from anyio import Path as AsyncPath
from colors import color
from discord import Embed, Message
from discord.ext.commands import BucketType, Cog, command, cooldown
from discord.utils import utcnow
from humanfriendly import format_size, format_timespan
from jishaku.math import mean_stddev
from psutil._common import bytes2human as format_bytes
from wavelink import InvalidNodeException, Pool

from bot.core import Context, Juno
from bot.shared import codeblock


class Information(Cog):
    process: psutil.Process
    metrics: dict[str, int]

    def __init__(self, bot: Juno) -> None:
        self.bot = bot
        self.process = psutil.Process()
        self.bot.loop.create_task(self.get_file_metrics())

    async def get_file_metrics(self):
        files = lines = classes = functions = comments = 0
        async for file in AsyncPath("./").rglob("*.py"):
            if ".venv" in file.parts:
                continue

            files += 1
            of = await file.open("r")
            for line in await of.readlines():
                line = line.strip()
                if line.startswith("class"):
                    classes += 1
                if line.startswith("def") or line.startswith("async def"):
                    functions += 1
                if "#" in line:
                    comments += 1

                lines += 1

        self.metrics = {
            "files": files,
            "lines": lines,
            "classes": classes,
            "functions": functions,
            "comments": comments,
        }

    def percentage_bar(self, percent: float, width: int = 11) -> str:
        return (
            "|"
            + color(
                f'{"█"*round(percent/(100/width)):-<{width}}',
                fg="magenta",
                bg="black",
            )
            + "|"
        )

    @command()
    @cooldown(1, 6, BucketType.channel)
    async def ping(self, ctx: Context) -> Optional[Message]:
        """View the bot's latency."""

        message: Optional[Message] = None
        embed = Embed(title="Round-Trip Latency")

        api_readings: List[float] = []
        websocket_readings: List[float] = []

        for _ in range(5):
            if api_readings:
                embed.description = (
                    ">>> ```bf\n"
                    + "\n".join(
                        f"Trip {index + 1}: {reading * 1000:.2f}ms"
                        for index, reading in enumerate(api_readings)
                    )
                    + "```"
                )

            text = ""

            if api_readings:
                average, stddev = mean_stddev(api_readings)

                text += f"Average: `{average * 1000:.2f}ms` `\N{PLUS-MINUS SIGN}` `{stddev * 1000:.2f}ms`"

            if websocket_readings:
                average = sum(websocket_readings) / len(websocket_readings)

                text += f"\nWebsocket Latency: `{average * 1000:.2f}ms`"
            else:
                text += f"\nWebsocket latency: `{self.bot.latency * 1000:.2f}ms`"

            if message:
                embed = message.embeds[0]
                embed.clear_fields()
                embed.add_field(
                    name="​",
                    value=text,
                )

                before = perf_counter()
                await message.edit(embed=embed)
                after = perf_counter()

                api_readings.append(after - before)
            else:
                embed.add_field(
                    name="​",
                    value=text,
                )

                before = perf_counter()
                message = await ctx.send(embed=embed)
                after = perf_counter()

                api_readings.append(after - before)

            if self.bot.latency > 0.0:
                websocket_readings.append(self.bot.latency)

        if message:
            return message

    @command(aliases=("boot", "startup"))
    async def uptime(self, ctx: Context) -> Message:
        """View the bot's uptime."""

        return await ctx.send(
            f"yea... i been peepin for ummm {format_timespan(utcnow() - self.bot.uptime, max_units=2)}"
        )

    @command(aliases=("inv",))
    async def invite(self, ctx: Context) -> Message:
        """View the bot's invite link."""

        discovery_url = "https://discord.com/application-directory"
        return await ctx.send(f"{discovery_url}/{self.bot.user.id}")

    @command(aliases=("discord",))
    async def support(self, ctx: Context) -> Message:
        """View the bot's support server invite."""

        return await ctx.send(self.bot.config.support.invite)

    @command(aliases=("botinfo", "bi"))
    async def about(self, ctx: Context) -> Message:
        """View information about the bot"""

        embed = Embed(
            description="\n".join(
                [
                    (
                        f"Serving `{len(self.bot.guilds):,}` servers"
                        f" with `{len(self.bot.users):,}` users"
                    ),
                    (
                        f"Utilizing `{len(set(self.bot.walk_commands())):,}` commands"
                        f" across `{len(self.bot.cogs)}` extensions"
                    ),
                ]
            ),
        )
        embed.set_author(
            name=self.bot.user.display_name,
            url=self.bot.config.support.invite,
            icon_url=self.bot.user.display_avatar,
        )
        embed.add_field(
            name="Process",
            value="\n".join(
                [
                    f"**CPU:** `{self.process.cpu_percent()}%`",
                    f"**RAM:** `{format_size(self.process.memory_info().rss)}`",
                    f"**Launched:** {discord.utils.format_dt(self.bot.uptime, 'R')}",
                ]
            ),
        )
        embed.add_field(
            name="Links",
            value="\n".join(
                [
                    f"[Invite]({discord.utils.oauth_url(self.bot.user.id)})",
                    f"[Website]({self.bot.config.website_url})",
                    f"[Discord Server]({self.bot.config.support})",
                ]
            ),
        )
        embed.set_footer(
            text=f"{self.bot.version} with discord.py v{discord.__version__}"
        )

        return await ctx.send(embed=embed)

    @command(aliases=("sys",))
    @cooldown(1, 6, BucketType.channel)
    async def system(self, ctx: Context) -> Message:
        """View system metrics."""

        try:
            node_version = await Pool.get_node().fetch_version()
        except InvalidNodeException:
            node_version = None

        if not self.metrics:
            return await ctx.send("slow down yn")

        metrics = self.metrics
        design = ctx.author.is_on_mobile() * "normal" or "galaxy"
        stars = ["o", "*", "•", ".", "+"]
        with open(f"bot/assets/ascii/{design}.txt", "r") as f:
            art = color(f.read(), fg="magenta")
            for star in stars:
                art = art.replace(star, f"[36m{star}[35m")

            embed = Embed(description=codeblock(art, "ansi"))

        embed.add_field(
            name="\u200b",
            value=codeblock(
                f"""
[36mPID-----[0m : [35m{self.process.pid:<9}[0m |[35m{self.process.name()}[0m
[36mCPU-----[0m : [35m{(f'{self.process.cpu_percent()}/100%'):<9}[0m {self.percentage_bar(self.process.cpu_percent())}
[36mRAM-----[0m : [35m{format_bytes(self.process.memory_info().rss):<9}[0m {self.percentage_bar(self.process.memory_percent())}
[36mDISK----[0m : [35m{format_bytes(psutil.disk_usage("/").used):<9}[0m {self.percentage_bar(psutil.disk_usage('/').percent)}
[36mUPTIME--[0m : [35m{format_timespan(utcnow() - self.bot.uptime, max_units=2)}[0m
""",
                "ansi",
            ),
            inline=False,
        )
        embed.add_field(
            name="\u200b",
            value=codeblock(
                f"""
[36mFILES---[0m : [35m{metrics['files']:,}[0m
[36mLINES---[0m : [35m{metrics["lines"]:,}[0m
[36mFUNCTS--[0m : [35m{metrics["functions"]:,}[0m
[36mCLASSES-[0m : [35m{metrics["classes"]:,}[0m
[36mCOMMENTS[0m : [35m{metrics["comments"]:,}[0m
[36mCMDS/CGS[0m : [35m{len(set(self.bot.walk_commands())):,}/{len(self.bot.cogs):,}[0m
""",
                "ansi",
            ),
            inline=True,
        )
        embed.add_field(
            name="\u200b",
            value=codeblock(
                f"""
[36mPYTHON--[0m : [35m{python_version()}[0m
[36mDISCORD-[0m : [35m{metadata.version("discord.py")[:6]}[0m
[36mASYNCPG-[0m : [35m{metadata.version("asyncpg")}[0m
[36mAIOHTTP-[0m : [35m{metadata.version("aiohttp")}[0m
[36mWAVELINK[0m : [35m{metadata.version("wavelink")[:5]}[0m
[36mLAVALINK[0m : [35m{node_version or 'N/A'}[0m
""",
                "ansi",
            ),
            inline=True,
        )

        return await ctx.send(embed=embed)


async def setup(bot: Juno) -> None:
    await bot.add_cog(Information(bot))
