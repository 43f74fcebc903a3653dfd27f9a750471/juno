from __future__ import annotations

import textwrap
import traceback
from contextlib import suppress
from datetime import datetime, timezone
from io import StringIO
from logging import getLogger
from pathlib import Path
from secrets import token_urlsafe
from socket import AF_INET
from typing import List, Literal, Optional, cast, get_args

import discord
from aiohttp import ClientSession, ContentTypeError, TCPConnector
from aiohttp_proxy import ProxyConnector
from asyncpraw import Reddit as RedditClient
from discord import (
    File,
    Guild,
    HTTPException,
    Member,
    Message,
    MessageType,
    StandardSticker,
    TextChannel,
    User,
)
from discord.app_commands import CommandTree
from discord.ext import commands
from discord.ext.commands import (
    AutoShardedBot,
    BucketType,
    CooldownMapping,
    ExtensionError,
    when_mentioned_or,
)
from discord.ext.tasks import loop
from discord.utils import get
from humanfriendly import format_timespan
from wavelink import InvalidNodeException
from cryptography.fernet import Fernet
from os import environ
from yarl import URL

from bot.core.database.settings import Settings
from bot.core.internal.ux import print_banner
from bot.shared import from_stack

from bot.shared.browser import BrowserManager
from bot.shared.client.context import Context, Reskin
from bot.shared.client.context.help import HelpCommand
from bot.shared.converters import PartialAttachment
from bot.shared.formatter import human_join, plural, short_timespan
from bot.shared.timer import Timer
from bot.types.config import Version
from config import Config

from .patch import reload_patches
from ..assets import fonts
from . import database
from .backend import Backend
from .backend.gateway.interfaces import PartialGuild
from .redis import Redis
from .tixte import Tixte
from shared_api.wrapper import SharedAPI

logger = getLogger("bot.core")
reload_patches()


class Juno(AutoShardedBot):
    config: Config
    version: Version
    uptime: datetime
    session: ClientSession
    user: discord.ClientUser
    db: database.Database
    db_version: str
    db_pid: int
    tixte: Tixte
    redis: Redis
    backend: Backend
    browser: BrowserManager
    global_cooldown: CooldownMapping
    traceback: dict[str, Exception] = {}
    reddit: Optional[RedditClient] = None
    wumpus_stickers: List[StandardSticker]
    fernet: Fernet
    api: SharedAPI

    def __init__(self, config: Config):
        super().__init__(
            case_insensitive=True,
            strip_after_prefix=True,
            intents=discord.Intents.all(),
            help_command=HelpCommand(),
            allowed_mentions=discord.AllowedMentions(
                everyone=False,
                roles=False,
                replied_user=False,
                users=True,
            ),
            command_prefix=self.get_prefixes,
            tree_cls=CommandTree,
            max_messages=300,
        )
        self.config = config
        self.version = config.version
        self.owner_ids = config.owner_ids
        self.global_cooldown = CooldownMapping.from_cooldown(2, 2.4, BucketType.user)
        self.backend = Backend(self)
        self.fernet = Fernet(environ["FERNET_KEY"])
        self.api = SharedAPI(config.api.shared)

    @property
    def lounge(self) -> Optional[TextChannel]:
        guild = self.get_guild(self.config.support.id)
        channel = guild and get(guild.text_channels, name="lounge")
        return channel

    async def start(self) -> None:
        await super().start(self.config.token, reconnect=True)

    async def close(self) -> None:
        await super().close()
        if not hasattr(self, "session"):
            return

        self.timer_task.cancel()
        await self.api.close()
        await self.session.close()
        await self.db.close()
        await self.redis.close()
        await self.browser.cleanup()

    @loop(minutes=1)
    async def timer_task(self) -> None:
        query = """
        DELETE FROM timer.task
        WHERE expires_at <= NOW()
        RETURNING *
        """
        records = await self.db.fetch(query)
        for record in records:
            timer = Timer(self, record)
            if timer.expired:
                self.dispatch(timer.event, timer)

    @classmethod
    async def get_prefixes(cls, bot: Juno, message: discord.Message):
        if not message.guild:
            return commands.when_mentioned(bot, message)

        usernames = [
            message.author.display_name,
            message.author.display_name.replace(" ", ""),
        ]
        settings = await Settings.fetch(bot, message.guild)
        prefixes = settings.prefixes.copy() or bot.config.prefixes.copy()
        if (
            bot.lounge
            and (member := bot.lounge.guild.get_member(message.author.id))
            and member.premium_since
        ):
            prefixes.extend(usernames)

        elif message.author.id in {*bot.config.owner_ids, 1264856750667075697}:
            prefixes.extend(usernames)

        return when_mentioned_or(*prefixes)(bot, message)

    def get_message(self, message_id: int) -> Optional[Message]:
        return self._connection._get_message(message_id)

    async def get_or_fetch_message(
        self,
        channel_id: int,
        message_id: int,
    ) -> Optional[Message]:
        if message := self.get_message(message_id):
            return message

        channel = cast(Optional[TextChannel], self.get_channel(channel_id))
        if not channel:
            return None

        return await channel.fetch_message(message_id)

    async def get_or_fetch_user(self, user_id: int) -> User:
        return self.get_user(user_id) or await self.fetch_user(user_id)

    async def say(self, *args, **kwargs) -> None:
        destination = from_stack("channel")
        if not destination:
            return

        return await destination.send(*args, **kwargs)

    async def setup_hook(self) -> None:
        self.session = ClientSession(
            headers={
                "User-Agent": "Mozilla/5.0 (iPhone; U; CPU iPhone OS 4_0 like Mac OS X; en-us)"
                " AppleWebKit/532.9 (KHTML, like Gecko) Version/4.0.5 Mobile/8A293 Safari/6531.22.7"
            },
            connector=ProxyConnector.from_url(self.config.http_proxy)
            if self.config.http_proxy
            else TCPConnector(family=AF_INET),
        )
        self.tixte = Tixte(self)
        self.db, self.db_version, self.db_pid = await database.connect()
        self.redis = await Redis.from_url()
        self.browser = await BrowserManager().setup()

        wumpus_pack = await self.fetch_premium_sticker_pack(847199849233514549)
        self.wumpus_stickers = wumpus_pack.stickers

    async def load_extensions(self) -> None:
        await self.load_extension("jishaku")
        for extension in Path("bot/extensions").glob("*"):
            if extension.name.startswith(("_", ".")):
                continue

            elif extension.is_file() and extension.suffix == ".py":
                package = extension.stem

            elif extension.is_dir() and (extension / "__init__.py").exists():
                package = extension.name

            else:
                continue

            try:
                await self.load_extension(f"bot.extensions.{package}")
            except ExtensionError as exc:
                logger.error(f"Error loading extension {package}", exc_info=exc)
            else:
                cog = next(reversed(self.cogs.values()), None)
                if cog:
                    commands = len(set(cog.walk_commands()))
                    events = len(cog.get_listeners())
                    logger.debug(
                        f"Loaded extension {cog.qualified_name} with {plural(commands):command}"
                        + (f" and {plural(events):event}" if events else "")
                    )

    async def get_context(
        self,
        origin: discord.Message | discord.Interaction,
        *,
        cls=None,
    ) -> Context:
        ctx = await super().get_context(origin, cls=cls or Context)
        if not ctx.guild:
            return ctx

        ctx.reskin = await Reskin.fetch(ctx)
        ctx.settings = await Settings.fetch(self, ctx.guild)
        return ctx

    @staticmethod
    async def cooldown_check(ctx: Context) -> Literal[True]:
        if ctx.author.id in list(ctx.bot.owner_ids or []):
            return True

        if ctx.command.cog_name == "Gamble" and ctx.channel.id == 1312642433904939009:
            return True
        
        bucket = ctx.bot.global_cooldown.get_bucket(ctx.message)
        if not bucket:
            return True

        retry_after = bucket.update_rate_limit()
        if retry_after:
            raise commands.CommandOnCooldown(bucket, retry_after, BucketType.user)

        return True

    async def process_commands(self, message: discord.Message) -> None:
        for original, replacement in fonts.freaky.items():
            message.content = message.content.replace(replacement, original)

        ctx = await self.get_context(message)
        if not all((ctx.guild, ctx.channel, not ctx.author.bot)):
            return

        permissions = ctx.channel.permissions_for(ctx.guild.me)
        if not all((permissions.send_messages, permissions.embed_links)):
            return

        if message.author.id in self.config.blacklist:
            if ctx.valid:
                logger.warning(
                    f"Blacklisted user {ctx.author} attempted to run {ctx.command} in {ctx.guild} ({ctx.guild.id})"
                )

            return

        if (
            ctx.invoked_with
            and isinstance(message.channel, discord.PartialMessageable)
            and message.channel.type != discord.ChannelType.private
        ):
            logger.warning(
                "Discarded a command message (ID: %s) with PartialMessageable channel: %r.",
                message.id,
                message.channel,
            )
        else:
            await self.invoke(ctx)

        if not ctx.valid:
            self.dispatch("message_without_command", ctx)
            if ctx.message.content and (
                self.user in ctx.message.mentions
                or ctx.replied_message
                and ctx.replied_message.author == self.user
            ):
                await self.invoke_clever(ctx)

    async def on_ready(self) -> None:
        if hasattr(self, "uptime"):
            return

        self.uptime = datetime.now(timezone.utc)
        self.check(self.cooldown_check)
        await self.load_extensions()

        print_banner(self)
        self.timer_task.start()
        self.backend.start_task()

    async def on_guild_join(self, guild: Guild) -> None:
        if guild.owner_id in self.config.blacklist:
            logger.warning(
                f"Leaving {guild.name} ({guild.id}) due to owner {guild.owner_id} being blacklisted"
            )
            with suppress(discord.HTTPException):
                await guild.leave()

            if self.lounge:
                await self.lounge.send(
                    f"lmaooo dumbass kid <@{guild.owner_id}> tried adding me to {guild.name}"
                )

            return

        if not self.lounge:
            return

        await self.lounge.send(
            f"Joined {guild.name} (`{guild.id}`) with {plural(len(guild.members)):member} owned by {guild.owner or 'Unknown User'} (`{guild.owner_id}`)"
        )

    async def on_guild_update(self, before: Guild, after: Guild):
        if not self.is_ready():
            return

        await self.backend.gateway.broadcast(
            after.id,
            {
                "event": "GUILD_UPDATE",
                "data": {
                    "before": PartialGuild.parse(before).model_dump(mode="json"),
                    "after": PartialGuild.parse(after).model_dump(mode="json"),
                },
            },
        )

    async def on_audit_log_entry_create(self, entry: discord.AuditLogEntry):
        if not self.is_ready():
            return

        event = f"audit_log_entry_{entry.action.name}"
        self.dispatch(event, entry)

    async def on_command_completion(self, ctx: Context) -> None:
        duration = discord.utils.utcnow() - ctx.message.created_at
        guild = textwrap.shorten(ctx.guild.name, width=20, placeholder="…")
        logger.info(
            f"{ctx.author} ran {ctx.command} in {guild} ({ctx.guild.id}) +{short_timespan(duration.total_seconds())}"
        )

        for converter in ctx.args:
            if isinstance(converter, PartialAttachment):
                converter.buffer = None  # type: ignore

        query = "INSERT INTO commands.usage (guild_id, channel_id, user_id, command) VALUES ($1, $2, $3, $4)"
        await self.db.execute(
            query,
            ctx.guild.id,
            ctx.channel.id,
            ctx.author.id,
            ctx.command.qualified_name,
        )

    async def on_command_error(
        self,
        ctx: Context,
        exc: commands.CommandError,
    ) -> Optional[discord.Message]:
        if not (
            ctx.channel.permissions_for(ctx.guild.me).send_messages
            and ctx.channel.permissions_for(ctx.guild.me).embed_links
        ):
            return

        if isinstance(
            exc,
            (
                commands.CommandNotFound,
                commands.DisabledCommand,
                commands.NotOwner,
            ),
        ):
            return

        elif isinstance(
            exc,
            (
                commands.MissingRequiredArgument,
                commands.MissingRequiredAttachment,
                commands.BadLiteralArgument,
            ),
        ):
            return await ctx.send_help(ctx.command)

        elif isinstance(exc, InvalidNodeException):
            return await ctx.warn("The bot is currently restarting, please wait..")

        elif isinstance(exc, commands.FlagError):
            if isinstance(exc, commands.MissingFlagArgument):
                return await ctx.warn(
                    f"The `{exc.flag.name}` flag is required for this command"
                )

            elif isinstance(exc, commands.MissingFlagArgument):
                return await ctx.warn(f"You did not specify the `{exc.flag.name}` flag")

            elif isinstance(exc, commands.TooManyFlags):
                return await ctx.warn(
                    f"You specified the `{exc.flag.name}` flag more than once"
                )

            elif isinstance(exc, commands.BadFlagArgument):
                try:
                    annotation = exc.flag.annotation.__name__
                except AttributeError:
                    annotation = exc.flag.annotation.__class__.__name__

                message = f"The `{exc.flag.name}` flag must be of type `{annotation}`"
                if annotation == "bool":
                    message = f"The `{exc.flag.name}` flag must be `true` or `false`"

                elif annotation == "Literal":
                    options = human_join(
                        [f"`{option}`" for option in get_args(exc.flag.annotation)]
                    )
                    message = f"The `{exc.flag.name}` flag must be {options}"

                elif annotation == "Range":
                    message = f"The `{exc.flag.name}` flag must be between `{exc.flag.annotation.min}` and `{exc.flag.annotation.max}`"

                return await ctx.warn(message)

        elif isinstance(exc, commands.MaxConcurrencyReached):
            if ctx.command.qualified_name.startswith(("role",)):
                return

            return await ctx.warn(
                f"This command can only be used {plural(exc.number):time}"
                f" per {exc.per.name} concurrently, please wait..",
                delete_after=5,
            )

        elif isinstance(exc, commands.CommandOnCooldown):
            if exc.retry_after > 30:
                return await ctx.warn(
                    f"This command is on cooldown, please wait {format_timespan(exc.retry_after)}",
                    delete_after=5,
                )

            return await ctx.message.add_reaction("⏳")

        elif isinstance(exc, commands.BadUnionArgument):
            if exc.converters == (discord.Member, discord.User):
                return await ctx.warn(
                    f"The provided {exc.param.name} is invalid"
                    f", try using their ID or mention instead",
                )

            elif exc.converters == (discord.Guild, discord.Invite):
                return await ctx.warn(
                    f"The provided {exc.param.name} is invalid"
                    f", try using the ID or invite URL instead",
                )

            else:
                return await ctx.warn(
                    f"The provided {exc.param.name} is invalid\n{exc}"
                )

        elif isinstance(exc, commands.MemberNotFound):
            return await ctx.warn("The provided member could not be found")

        elif isinstance(exc, commands.UserNotFound):
            return await ctx.warn("The provided user could not be found")

        elif isinstance(exc, commands.ChannelNotFound):
            return await ctx.warn("The provided channel could not be found")

        elif isinstance(exc, commands.RoleNotFound):
            return await ctx.warn("The provided role could not be found")

        elif isinstance(exc, commands.BadInviteArgument):
            return await ctx.warn("The provided invite is invalid")

        elif isinstance(exc, commands.MessageNotFound):
            return await ctx.warn(
                "The provided message could not be found"
                ", try using the message URL instead"
            )

        elif isinstance(exc, commands.RangeError):
            label = ""
            if exc.minimum is None and exc.maximum is not None:
                label = f"no more than `{exc.maximum}`"
            elif exc.minimum is not None and exc.maximum is None:
                label = f"no less than `{exc.minimum}`"
            elif exc.maximum is not None and exc.minimum is not None:
                label = f"between `{exc.minimum}` and `{exc.maximum}`"

            if label and isinstance(exc.value, str):
                label += " characters"

            return await ctx.warn(f"The input must be {label}")

        elif isinstance(exc, commands.MissingPermissions):
            permissions = human_join(
                [
                    f"`{permission.replace('_', ' ').title()}`"
                    for permission in exc.missing_permissions
                ],
                final="and",
            )
            _plural = "permission" + (len(exc.missing_permissions) > 1) * "s"

            return await ctx.warn(f"You are missing the {permissions} {_plural}")

        elif isinstance(exc, commands.BadArgument):
            return await ctx.warn(exc.args[0])

        elif isinstance(exc, commands.CommandInvokeError):
            original = exc.original
            if isinstance(original, ContentTypeError):
                return await ctx.warn("No response was received from the API")

            elif isinstance(original, HTTPException):
                if original.code == 50045:
                    return await ctx.warn("The provided asset is too large to upload")

            logger.error("Error invoking command: %s", exc, exc_info=original)
            if ctx.author.id not in self.config.owner_ids:
                identifier = token_urlsafe(12)
                self.traceback[identifier] = original

                return await ctx.send(
                    f"i have no idea what happened just send ethan the following code please.... `{identifier}`"
                )
            else:
                fmt = "\n".join(traceback.format_exception(original))
                if len(fmt) > 1900:
                    return await ctx.author.send(
                        file=File(
                            StringIO(fmt),  # type: ignore
                            filename="error.py",
                        ),
                    )

                return await ctx.author.send(content=fmt)

        elif isinstance(exc, commands.CommandError):
            if isinstance(exc, commands.CheckFailure):
                origin = getattr(exc, "original", exc)
                with suppress(TypeError):
                    if any(
                        forbidden in origin.args[-1]
                        for forbidden in (
                            "global check",
                            "check functions",
                            "Unknown Channel",
                            "Us",
                        )
                    ):
                        return

            arguments: List[str] = []
            for argument in exc.args:
                if isinstance(argument, str):
                    arguments.append(argument)

                elif isinstance(argument, (TypeError, ValueError)):
                    arguments.extend(argument.args)

            if not arguments:
                logger.error("Error invoking command: %s", exc, exc_info=exc)
                if ctx.author.id not in self.config.owner_ids:
                    identifier = token_urlsafe(12)
                    self.traceback[identifier] = exc

                    return await ctx.send(
                        f"i have no idea what happened just send ethan the following code please.... `{identifier}`"
                    )
                else:
                    fmt = "\n".join(traceback.format_exception(exc))
                    if len(fmt) > 1900:
                        return await ctx.author.send(
                            file=File(
                                StringIO(fmt),  # type: ignore
                                filename="error.py",
                            ),
                        )

                    return await ctx.author.send(content=fmt)

            return await ctx.warn("\n".join(arguments).split("Error:")[-1])

    async def on_member_update(self, before: Member, after: Member) -> None:
        if after.guild.system_channel_flags.premium_subscriptions:
            return

        if not before.premium_since and after.premium_since:
            self.dispatch("member_boost", after)

        elif before.premium_since and not after.premium_since:
            self.dispatch("member_unboost", before)

    async def on_member_remove(self, member: Member) -> None:
        if member == self.user:
            return

        if member.premium_since:
            self.dispatch("member_unboost", member)

    async def on_typing(
        self,
        channel: TextChannel,
        user: Member | User,
        when: datetime,
    ) -> None:
        if isinstance(user, Member):
            self.dispatch("member_activity", channel, user)

    async def on_message_edit(
        self,
        before: discord.Message,
        after: discord.Message,
    ) -> None:
        self.dispatch("member_activity", after.channel, after.author)
        if before.content == after.content:
            return

        await self.process_commands(after)

    async def on_message(self, message: discord.Message) -> None:
        if (
            message.guild
            and message.guild.system_channel_flags.premium_subscriptions
            and message.type
            in (
                MessageType.premium_guild_subscription,
                MessageType.premium_guild_tier_1,
                MessageType.premium_guild_tier_2,
                MessageType.premium_guild_tier_3,
            )
        ):
            self.dispatch("system_boost", message)
            self.dispatch("member_boost", message.author)

        self.dispatch("member_activity", message.channel, message.author)
        return await super().on_message(message)

    async def invoke_clever(self, ctx: Context) -> None:
        resource = f"cleverbot:{ctx.author.id}"
        ratelimited = await self.redis.ratelimited(resource, 5, 30)
        if ratelimited:
            return

        logger.info(
            f"Invoking clever bot for {ctx.author} in {ctx.guild} ({ctx.guild.id})"
        )
        cs_resource = f"cleverbot:conversation:{ctx.channel.id}"
        conversation_id = cast(Optional[str], await self.redis.get(cs_resource))
        response = await self.session.get(
            URL.build(
                scheme="https",
                host="www.cleverbot.com",
                path="/getreply",
                query={
                    "input": ctx.message.clean_content.replace(
                        f"@{ctx.guild.me.display_name}",
                        "",
                    ).strip(),
                    "cs": conversation_id or "",
                    "key": "CC9db9SL-aX3lL2t0GLBfTTkTug",
                },
            ),
        )
        data = await response.json()
        if not data.get("output"):
            return

        if not conversation_id:
            await self.redis.set(cs_resource, data["cs"], ex=300)

        await ctx.reply(data["output"], allowed_mentions=discord.AllowedMentions.none())
