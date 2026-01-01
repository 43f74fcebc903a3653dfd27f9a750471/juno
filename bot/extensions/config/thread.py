from contextlib import suppress
from logging import getLogger
from typing import Optional

from discord import Embed, HTTPException, Message
from discord import Thread as ThreadChannel
from discord.ext.commands import Cog, group, has_permissions, parameter

from bot.core import Context, Juno
from bot.core.database.settings import Settings
from bot.shared.paginator import Paginator

logger = getLogger("bot.threads")


class Thread(Cog):
    def __init__(self, bot: Juno) -> None:
        self.bot = bot

    @Cog.listener()
    async def on_thread_update(
        self,
        before: ThreadChannel,
        thread: ThreadChannel,
    ) -> None:
        """Prevent threads from being automatically archived."""

        if not thread.archived:
            return

        settings = await Settings.fetch(self.bot, thread.guild)
        if thread.id not in settings.record["monitored_threads"]:
            return

        with suppress(HTTPException):
            await thread.edit(
                archived=False,
                auto_archive_duration=10080,
                reason="This thread is being monitored for archival",
            )
            logger.info(
                f"Unarchived thread {thread.id} in {thread.guild} ({thread.guild.id})"
            )

    @group(aliases=("watcher",), invoke_without_command=True)
    @has_permissions(manage_threads=True)
    async def thread(self, ctx: Context) -> Message:
        """Prevent threads from being automatically archived."""

        return await ctx.send_help(ctx.command)

    @thread.command(name="add", aliases=("create", "new"))
    @has_permissions(manage_threads=True)
    async def thread_add(self, ctx: Context, *, thread: ThreadChannel) -> Message:
        """Add a thread to be monitored."""

        if thread in ctx.settings.monitored_threads:
            return await ctx.warn(f"{thread.mention} is already being monitored")

        ctx.settings.record["monitored_threads"].append(thread.id)
        await ctx.settings.upsert()
        return await ctx.approve(f"Now monitoring {thread.mention} for archival")

    @thread.command(name="remove", aliases=("delete", "del", "rm"))
    @has_permissions(manage_threads=True)
    async def thread_remove(self, ctx: Context, *, thread: ThreadChannel) -> Message:
        """Remove a thread from being monitored."""

        if thread not in ctx.settings.monitored_threads:
            return await ctx.warn(f"{thread.mention} is not being monitored")

        ctx.settings.record["monitored_threads"].remove(thread.id)
        await ctx.settings.upsert()
        return await ctx.approve(f"No longer monitoring {thread.mention} for archival")

    @thread.command(name="clear")
    @has_permissions(manage_threads=True)
    async def thread_clear(self, ctx: Context) -> Message:
        """Remove all threads from being monitored."""

        await ctx.prompt(
            "Are you sure you want to remove all threads from being monitored?"
        )

        ctx.settings.record["monitored_threads"].clear()
        await ctx.settings.upsert()
        return await ctx.approve("No longer monitoring any threads")

    @thread.command(name="list")
    @has_permissions(manage_threads=True)
    async def thread_list(self, ctx: Context) -> Message:
        """View all threads being monitored."""

        threads = [
            f"{thread.mention} [`{thread.id}`]"
            for thread in ctx.settings.monitored_threads
        ]
        if not threads:
            return await ctx.warn("No threads are being monitored")

        embed = Embed(title="Monitored Threads")
        paginator = Paginator(ctx, threads, embed)
        return await paginator.start()

    @thread.command(
        name="archive",
        aliases=(
            "lock",
            "close",
        ),
    )
    @has_permissions(manage_threads=True)
    async def thread_archive(
        self,
        ctx: Context,
        *,
        thread: ThreadChannel = parameter(default=lambda ctx: ctx.channel),
    ) -> Optional[Message]:
        """Archive a thread to prevent further messages."""

        if not isinstance(thread, ThreadChannel):
            return await ctx.warn("This command only works in threads")

        if thread.archived or thread.locked:
            return await ctx.warn(f"{thread.mention} is already archived")

        await ctx.add_check()
        await thread.edit(
            locked=True,
            archived=True,
            reason=f"Archived by {ctx.author} ({ctx.author.id})",
        )

    @thread.command(
        name="unarchive",
        aliases=(
            "unlock",
            "open",
        ),
    )
    @has_permissions(manage_threads=True)
    async def thread_unarchive(
        self,
        ctx: Context,
        *,
        thread: ThreadChannel = parameter(default=lambda ctx: ctx.channel),
    ) -> Optional[Message]:
        """Unarchive a thread to allow further messages."""

        if not isinstance(thread, ThreadChannel):
            return await ctx.warn("This command only works in threads")

        if not thread.archived or not thread.locked:
            return await ctx.warn(f"{thread.mention} is not archived")

        await ctx.add_check()
        await thread.edit(
            locked=False,
            archived=False,
            reason=f"Unarchived by {ctx.author} ({ctx.author.id})",
        )
