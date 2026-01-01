from contextlib import suppress
from io import BytesIO
from typing import Optional

from discord import Embed, File, HTTPException, Member, Message, TextChannel, Thread
from discord.utils import MISSING

from bot.shared import Script, codeblock

__all__ = ("notify_failure",)


async def notify_failure(
    event: str,
    member: Member,
    channel: TextChannel | Thread,
    script: Script,
    exc: HTTPException,
) -> Optional[Message]:
    """Notify the server owner of a system message failure."""

    owner = member.guild.owner
    if not owner:
        return

    embed = Embed(
        title=f"{event.title()} Failure",
        description=(
            f"Could not send a {event} message for `{member}` in {channel.mention}\n"
            + codeblock(exc.text.split("Body", 1)[-1].strip(), "yaml")
        ),
    )
    file = MISSING
    if len(script.template) <= 1024:
        embed.add_field(name="Script", value=codeblock(script.template, "yaml"))
    else:
        file = File(BytesIO(script.template.encode()), filename="script.txt")

    with suppress(HTTPException):
        return await owner.send(embed=embed, file=file)
