from typing import Annotated, List, TypedDict
from discord import Embed, Message
from jishaku.codeblocks import Codeblock, codeblock_converter
from discord.ext.commands import Cog, group, max_concurrency, BucketType
from discord.utils import as_chunks
from aiohttp import ClientSession
from yarl import URL
from bot.core import Juno, Context
from cashews import cache

from bot.shared.paginator import Paginator


class Runtime(TypedDict):
    language: str
    version: str
    aliases: List[str]


class Result(TypedDict):
    stdout: str
    stderr: str
    code: int
    output: str


@cache(ttl="5m")
async def fetch_runtimes() -> List[Runtime]:
    async with ClientSession() as session:
        async with session.get(
            URL.build(scheme="https", host="emkc.org", path="/api/v2/piston/runtimes")
        ) as response:
            return await response.json()


@cache(ttl="5m")
async def execute(runtime: Runtime, code: str) -> Result:
    async with ClientSession() as session:
        async with session.post(
            URL.build(scheme="https", host="emkc.org", path="/api/v2/piston/execute"),
            json={
                "language": runtime["language"],
                "version": runtime["version"],
                "files": [
                    {
                        "name": "main",
                        "content": code,
                    },
                ],
            },
        ) as response:
            data = await response.json()
            return data["run"]


class Piston(Cog):
    def __init__(self, bot: Juno) -> None:
        self.bot = bot

    @group(
        aliases=(
            "compile",
            "build",
            "eval",
            "run",
        ),
        invoke_without_command=True,
    )
    @max_concurrency(1, BucketType.user)
    async def piston(
        self,
        ctx: Context,
        *,
        code: Annotated[
            Codeblock,
            codeblock_converter,
        ],
    ) -> Message:
        """Evaluate code through a private Piston instance.

        The default runtime language is python, however you can change this by
        wrapping your code inside of a code block with the language you want to use.
        You can also view a list available languages with `piston runtimes` command.

        > Below is a **Hello world** example using `go`
        ```go
        package main
        import \"fmt\"

        func main() {
            fmt.Print(\"Hello world\")
        }```
        """

        async with ctx.typing():
            language = code.language or "python"

            runtimes = await fetch_runtimes()
            runtime = next(
                (
                    runtime
                    for runtime in runtimes
                    if language.lower() == runtime["language"]
                    or language.lower() in runtime["aliases"]
                ),
                None,
            )
            if not runtime:
                return await ctx.warn(
                    f"Invalid language provided, use `{ctx.clean_prefix}{ctx.invoked_with} runtimes` to view available languages"
                )

            result = await execute(runtime, code.content)

        embeds: List[Embed] = []
        for chunk in as_chunks(result["output"], 2000):
            chunk = "".join(chunk)

            embed = Embed(
                description=(
                    f"> Compiled `{runtime['language']}` code\n"
                    f"```{runtime["language"]}\n{chunk}```"
                ),
            )
            embeds.append(embed)

        if not embeds:
            return await ctx.warn("No output was returned")

        paginator = Paginator(ctx, embeds)
        return await paginator.start()

    @piston.command(
        name="runtimes",
        aliases=[
            "versions",
            "languages",
            "langs",
        ],
    )
    async def piston_runtimes(self, ctx: Context) -> Message:
        """View the available Piston runtimes."""

        runtimes = [
            (
                f"**{runtime['language']}** (`v{runtime['version']}`)"
                + (
                    f" | *{', '.join(runtime['aliases'])}*"
                    if runtime["aliases"]
                    else ""
                )
            )
            for runtime in await fetch_runtimes()
        ]

        embed = Embed(title="Piston Runtimes")
        paginator = Paginator(ctx, runtimes, embed)
        return await paginator.start()
