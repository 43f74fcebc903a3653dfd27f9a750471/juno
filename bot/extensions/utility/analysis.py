import asyncio
from typing import Any, Optional
from discord import Embed, Message
from discord.ext.commands import Cog, BucketType, parameter, flag, command, cooldown
from bot.core import Juno, Context
from bot.shared.converters import FlagConverter
from bot.shared.converters.attachment import PartialAttachment
from yarl import URL

from bot.shared.formatter import plural

BASE_URL = URL.build(
    scheme="https",
    host="virustotal.com",
    path="/api/v3",
)


class AnalysisFlags(FlagConverter):
    password: Optional[str] = flag(
        aliases=["pass", "pwd"],
        description="Password for a protected archive.",
    )


class Analysis(Cog):
    def __init__(self, bot: Juno) -> None:
        self.bot = bot

    @command(aliases=("analysis", "scan", "virus"))
    @cooldown(1, 10, BucketType.user)
    async def analyze(
        self,
        ctx: Context,
        attachment: PartialAttachment = parameter(
            default=lambda ctx: PartialAttachment.fallback(
                ctx, ("application", "image")
            ),
        ),
        *,
        flags: AnalysisFlags,
    ) -> Message:
        """Submit an attachment for malware analysis."""

        await ctx.respond("Submitting attachment for analysis...")

        async with ctx.typing():
            data: dict[str, Any] = {"file": attachment.buffer}
            if flags.password:
                data["password"] = flags.password

            response = await self.bot.session.post(
                BASE_URL / "files",
                headers={"x-apikey": self.bot.config.api.virus_total},
                data=data,
            )
            if not response.ok:
                return await ctx.warn(
                    "There was an error submitting your file", delete_response=True
                )

            data = await response.json()
            await ctx.respond(
                "Submitted file for analysis, please wait a moment..",
                delete_response=True,
            )

            for _ in range(8):
                response = await self.bot.session.get(
                    BASE_URL / "analyses" / data["data"]["id"],
                    headers={"x-apikey": self.bot.config.api.virus_total},
                )
                if response.ok:
                    data = await response.json()
                    if data["data"]["attributes"]["status"] == "completed":
                        data = data
                        break

                else:
                    return await ctx.warn(
                        "There was an error retrieving the analysis",
                        delete_response=True,
                    )

                await asyncio.sleep(5)

            else:
                return await ctx.warn(
                    "The analysis took too long to complete", delete_response=True
                )

            response = await self.bot.session.get(
                BASE_URL / "files" / data["meta"]["file_info"]["sha256"],
                headers={"x-apikey": self.bot.config.api.virus_total},
            )
            if not response.ok:
                return await ctx.warn(
                    "There was an error retrieving the analysis", delete_response=True
                )

            data = await response.json()
            data = data["data"]["attributes"]

        embed = Embed(
            url=f"https://www.virustotal.com/gui/file/{data['sha256']}/detection",
            title="Malware Analysis",
            description=f"`{data['last_analysis_stats']['malicious']}`/`{len(data['last_analysis_results'])}` security vendors flagged this file as malicious.",
        )
        embed.set_footer(text=", ".join(data["tags"]))

        results = sorted(
            data["last_analysis_results"].items(),
            key=lambda item: item[1]["category"] == "malicious",
            reverse=True,
        )
        embed.add_field(
            name="Engine Results",
            value="\n".join(
                [
                    f"**{engine}** `{item['result'] or 'Undetected'}`"
                    for engine, item in results
                ][:13]
            )
            + (
                f"\n... and {plural(len(results) - 13):more}"
                if len(results) > 13
                else ""
            ),
        )
        return await ctx.send(embed=embed, delete_response=True)
