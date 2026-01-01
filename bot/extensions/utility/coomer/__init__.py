import asyncio
from datetime import timedelta
from logging import getLogger
from typing import List, Optional

import discord
from discord import File, HTTPException, Message
from discord.ext.commands import (
    BucketType,
    Cog,
    command,
    has_permissions,
    max_concurrency,
)
from yarl import URL

from bot.core import Context, Juno
from bot.shared import temp_thread
from bot.shared.formatter import plural, shorten

from .model import Post

logger = getLogger("bot.coomer")


class Coomer(Cog):
    def __init__(self, bot: Juno) -> None:
        self.bot = bot

    @command(aliases=("onlyfans", "of", "patreon"))
    @has_permissions(manage_channels=True)
    @max_concurrency(1, BucketType.guild)
    async def coomer(self, ctx: Context, *, creator: str) -> Optional[Message]:
        """Redistribute a OnlyFans creator's content to a thread."""

        service = "onlyfans" if ctx.invoked_with != "patreon" else "patreon"
        creator = creator.replace("_", " ").lower()

        response = await self.bot.session.get(
            URL.build(
                scheme="https",
                host="coomer.su",
                path=f"/api/v1/{service}/user/{creator}",
            )
        )
        if not response.ok:
            return await ctx.warn(
                "The creator provided wasn't found, try to be more specific"
            )

        posts = await response.json()
        if not posts:
            return await ctx.warn("The creator provided has no posts")

        async with temp_thread(
            ctx,
            name=f"{creator}'s {service} posts",
            duration=timedelta(hours=1),
        ) as thread:
            await thread.send("Starting the upload process, please wait...")

            for post in posts:
                post = Post.model_validate(post)
                attachments = post.attachments or post.files
                if not attachments:
                    logger.debug(
                        f"Skipping {shorten(post.title)} because it doesn't have any attachments"
                    )
                    continue

                prepared: List[File] = await asyncio.gather(
                    *[
                        attachment.read(self.bot)
                        for attachment in attachments
                        if attachment.extension == "jpg"
                    ]
                )
                logger.debug(
                    f"Prepared {plural(len(prepared)):attachment} for {shorten(post.title)}"
                )
                if not prepared and any(
                    attachment.extension != "jpg" for attachment in attachments
                ):
                    await thread.send(post.url)
                    continue

                for chunk in discord.utils.as_chunks(prepared, 5):
                    title = shorten(post.title) if chunk[0] == prepared[0] else None
                    try:
                        await thread.send(content=title, files=chunk)
                    except HTTPException as e:
                        if e.code == 10003:
                            return await ctx.warn(
                                "Ending the task early, the thread appears to be deleted"
                            )

        return await ctx.approve(
            f"The upload process has finished for {thread.jump_url}"
        )
