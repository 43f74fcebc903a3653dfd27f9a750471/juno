from typing import cast
import zon
from aiohttp.web import json_response
from discord import Message
from discord.ext.commands import Cog, group, has_permissions

from bot.core import Context, Juno
from bot.core.backend.gateway.interfaces import PartialGuild
from bot.core.backend.oauth import OAuthRequest
from bot.core.backend.oauth import has_permissions as has_oauth_permissions
from bot.core.database.settings import Settings, Record
from bot.shared.formatter import human_join, plural

from .command import CommandManagement
from .gallery import Gallery
from .logging import Logging
from .publisher import Publisher
from .reskin import Reskin
from .roles import Roles
from .security import Security
from .starboard import Starboard
from .sticky import Sticky
from .system import System
from .thread import Thread
from .tickets import Tickets
from .trigger import Triggers
from .voicemaster import VoiceMaster
from .whitelist import Whitelist
from .webhook import Webhook


class Configuration(
    CommandManagement,
    Gallery,
    Logging,
    Publisher,
    Reskin,
    Webhook,
    Security,
    Starboard,
    Sticky,
    System,
    VoiceMaster,
    Whitelist,
    Roles,
    Triggers,
    Thread,
    Tickets,
    Cog,
):
    def __init__(self, bot: Juno) -> None:
        self.bot = bot
        for method, route, handler in (
            ("GET", "/@auth/guilds/{guild_id}", self.oauth_guild),
            ("PATCH", "/@auth/guilds/{guild_id}", self.oauth_guild_update),
        ):
            self.bot.backend.router.add_route(method, route, handler)  # type: ignore

    @has_oauth_permissions("manage_guild")
    async def oauth_guild(self, request: OAuthRequest):
        settings = await Settings.fetch(self.bot, request.guild)
        return json_response(
            {
                "guild": PartialGuild.parse(request.guild).model_dump(mode="json"),
                "settings": settings.record,
            }
        )

    @has_oauth_permissions("manage_guild")
    async def oauth_guild_update(self, request: OAuthRequest):
        data = cast(Record, await request.json())
        try:
            Settings.schema.validate(data)
        except zon.ZonError as exc:
            return json_response({"error": exc.issues}, status=400)

        settings = await Settings.fetch(self.bot, request.guild)
        await settings.upsert(**data)
        return json_response(settings.record)

    @group(invoke_without_command=True, aliases=("prefixes",))
    async def prefix(self, ctx: Context) -> Message:
        """View or change the bot's prefixes."""

        prefixes = ctx.settings.prefixes or ctx.bot.config.prefixes.copy()
        human_prefixes = human_join([f"`{prefix}`" for prefix in prefixes], final="and")

        return await ctx.respond(
            f"The prefixes for this server are {human_prefixes}"
            if len(prefixes) > 1
            else f"The prefix for this server is {human_prefixes}"
        )

    @prefix.command(name="set")
    @has_permissions(manage_guild=True)
    async def prefix_set(self, ctx: Context, prefix: str) -> Message:
        """Set the server's prefix."""

        if not prefix:
            return await ctx.warn("You must provide a prefix to set")

        if ctx.settings.prefixes:
            await ctx.prompt(
                f"Are you sure you want to set the server's prefix to `{prefix}`",
                f"This will override {plural(len(ctx.settings.prefixes), '`'):existing prefix} which {'is' if len(ctx.settings.prefixes) == 1 else 'are'} currently set",
            )

        await ctx.settings.upsert(prefixes=[prefix])
        return await ctx.approve(f"The server's prefix has been set to `{prefix}`")

    @prefix.command(name="add")
    @has_permissions(manage_guild=True)
    async def prefix_add(self, ctx: Context, prefix: str) -> Message:
        """Add a prefix to the server's prefixes."""

        if not prefix:
            return await ctx.warn("You must provide a prefix to add")

        elif prefix in ctx.settings.prefixes:
            return await ctx.warn(
                f"The prefix `{prefix}` is already in the server's prefixes"
            )

        prefixes = ctx.settings.prefixes or ctx.bot.config.prefixes.copy()
        prefixes.append(prefix)

        await ctx.settings.upsert(prefixes=prefixes)
        return await ctx.approve(f"Now accepting the prefix `{prefix}`")

    @prefix.command(name="remove", aliases=("delete", "del", "rm"))
    @has_permissions(manage_guild=True)
    async def prefix_remove(self, ctx: Context, prefix: str) -> Message:
        """Remove a prefix from the server's prefixes."""

        if not prefix:
            return await ctx.warn("You must provide a prefix to remove")

        elif prefix not in ctx.settings.prefixes:
            return await ctx.warn(
                f"The prefix `{prefix}` is not in the server's prefixes"
            )

        prefixes = ctx.settings.prefixes or ctx.bot.config.prefixes.copy()
        prefixes.remove(prefix)

        await ctx.settings.upsert(prefixes=prefixes)
        return await ctx.approve(f"The prefix `{prefix}` has been removed")

    @prefix.command(name="reset")
    @has_permissions(manage_guild=True)
    async def prefix_reset(self, ctx: Context) -> Message:
        """Reset the server's prefixes to the default."""

        await ctx.settings.upsert(prefixes=[])
        return await ctx.approve("The server's prefixes have been reset to the default")


async def setup(bot: Juno) -> None:
    await bot.add_cog(Configuration(bot))
