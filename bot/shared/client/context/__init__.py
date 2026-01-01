from __future__ import annotations

import asyncio
from secrets import token_urlsafe
from types import TracebackType
from typing import (
    TYPE_CHECKING,
    Any,
    Literal,
    Optional,
    Self,
    Sequence,
    TypeVar,
    Union,
    cast,
    no_type_check,
    overload,
)

import discord
from aiohttp import ClientSession
from cashews import cache
from discord import (
    AllowedMentions,
    File,
    Guild,
    GuildSticker,
    HTTPException,
    Member,
    Message,
    MessageReference,
    PartialMessage,
    Poll,
    StickerItem,
    TextChannel,
    Thread,
    WebhookMessage,
)
from discord.abc import MISSING
from discord.context_managers import Typing as OriginalTyping
from discord.ext import commands
from discord.ui import View

from bot.core.database.settings import Settings
from bot.shared import dominant_color, from_stack, quietly_delete

from .reskin import Reskin
from .reskin.guild import GuildReskin

if TYPE_CHECKING:
    from bot.core import Juno

__all__ = ("Context", "Reskin", "GuildReskin")
BE = TypeVar("BE", bound=BaseException)


class Typing(OriginalTyping):
    ctx: Context

    def __init__(self, ctx: Context) -> None:
        super().__init__(ctx.channel)
        self.ctx = ctx

    @property
    def reskinned(self) -> bool:
        if self.ctx.reskin and self.ctx.reskin.status:
            return True

        return False

    async def wrapped_typer(self) -> None:
        if not self.reskinned:
            return await super().wrapped_typer()

    async def do_typing(self) -> None:
        if not self.reskinned:
            return await super().do_typing()

    async def __aenter__(self) -> None:
        if not self.reskinned:
            return await super().__aenter__()

    async def __aexit__(
        self, exc_type: type[BE] | None, exc: BE | None, traceback: TracebackType | None
    ) -> None:
        if not hasattr(self, "task"):
            return

        return await super().__aexit__(exc_type, exc, traceback)


class Confirmation(View):
    ctx: Context
    status: Optional[bool] = None

    def __init__(self, ctx: Context) -> None:
        super().__init__(timeout=30)
        self.ctx = ctx

    async def on_timeout(self) -> None:
        self.status = False
        self.stop()

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return self.ctx.author.id == interaction.user.id

    @discord.ui.button(label="Confirm", style=discord.ButtonStyle.green)
    async def confirm(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ):
        self.status = True
        self.stop()

    @discord.ui.button(label="Decline", style=discord.ButtonStyle.danger)
    async def decline(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ):
        self.status = False
        self.stop()


class Context(commands.Context["Juno"]):
    id: str
    bot: Juno
    guild: Guild
    author: Member
    channel: TextChannel
    command: commands.Command[Any, ..., Any]
    reskin: Optional[Reskin]
    settings: Settings
    response: Optional[Message | WebhookMessage]

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.id = token_urlsafe(16)
        self.response = None

    def typing(self) -> Typing:
        return Typing(self)

    @property
    def session(self) -> ClientSession:
        return self.bot.session

    @discord.utils.cached_property
    def replied_message(self) -> Message | None:
        ref = self.message.reference
        if ref and isinstance(ref.resolved, Message):
            return ref.resolved

    @cache(ttl="2h", key="reskin:webhook:{self.guild.id}:{self.channel.id}")
    async def reskin_webhook(self) -> Optional[discord.Webhook]:
        channel = self.channel
        if not isinstance(channel, (TextChannel, Thread)):
            return

        elif isinstance(channel, Thread):
            channel = channel.parent
            if not isinstance(channel, TextChannel):
                return

        query = "SELECT webhook_id FROM reskin.webhook WHERE status = TRUE AND channel_id = $1"
        webhook_id = await self.bot.db.fetchval(query, channel.id)
        if not webhook_id:
            return

        webhooks = await channel.webhooks()
        webhook = discord.utils.get(webhooks, id=webhook_id)
        if webhook:
            return webhook

        query = "DELETE FROM reskin.webhook WHERE channel_id = $1"
        await self.bot.db.execute(query, channel.id)

        async def clear_cache():
            await asyncio.sleep(0.2)
            await cache.delete(f"reskin:webhook:{self.guild.id}:{self.channel.id}")

        asyncio.create_task(clear_cache())

    @overload
    async def send(
        self,
        content: Optional[str] = ...,
        *,
        tts: bool = ...,
        embed: discord.Embed = ...,
        file: File = ...,
        stickers: Sequence[Union[GuildSticker, StickerItem]] = ...,
        delete_after: float = ...,
        nonce: Union[str, int] = ...,
        allowed_mentions: AllowedMentions = ...,
        reference: Union[Message, MessageReference, PartialMessage] = ...,
        mention_author: bool = ...,
        view: View = ...,
        suppress_embeds: bool = ...,
        ephemeral: bool = ...,
        silent: bool = ...,
        poll: Poll = ...,
        edit_response: bool = ...,
        delete_response: bool = ...,
    ) -> Message: ...

    @overload
    async def send(
        self,
        content: Optional[str] = ...,
        *,
        tts: bool = ...,
        embed: discord.Embed = ...,
        files: Sequence[File] = ...,
        stickers: Sequence[Union[GuildSticker, StickerItem]] = ...,
        delete_after: float = ...,
        nonce: Union[str, int] = ...,
        allowed_mentions: AllowedMentions = ...,
        reference: Union[Message, MessageReference, PartialMessage] = ...,
        mention_author: bool = ...,
        view: View = ...,
        suppress_embeds: bool = ...,
        ephemeral: bool = ...,
        silent: bool = ...,
        poll: Poll = ...,
        edit_response: bool = ...,
        delete_response: bool = ...,
    ) -> Message: ...

    @overload
    async def send(
        self,
        content: Optional[str] = ...,
        *,
        tts: bool = ...,
        embeds: Sequence[discord.Embed] = ...,
        file: File = ...,
        stickers: Sequence[Union[GuildSticker, StickerItem]] = ...,
        delete_after: float = ...,
        nonce: Union[str, int] = ...,
        allowed_mentions: AllowedMentions = ...,
        reference: Union[Message, MessageReference, PartialMessage] = ...,
        mention_author: bool = ...,
        view: View = ...,
        suppress_embeds: bool = ...,
        ephemeral: bool = ...,
        silent: bool = ...,
        poll: Poll = ...,
        edit_response: bool = ...,
        delete_response: bool = ...,
    ) -> Message: ...

    @overload
    async def send(
        self,
        content: Optional[str] = ...,
        *,
        tts: bool = ...,
        embeds: Sequence[discord.Embed] = ...,
        files: Sequence[File] = ...,
        stickers: Sequence[Union[GuildSticker, StickerItem]] = ...,
        delete_after: float = ...,
        nonce: Union[str, int] = ...,
        allowed_mentions: AllowedMentions = ...,
        reference: Union[Message, MessageReference, PartialMessage] = ...,
        mention_author: bool = ...,
        view: View = ...,
        suppress_embeds: bool = ...,
        ephemeral: bool = ...,
        silent: bool = ...,
        poll: Poll = ...,
        edit_response: bool = ...,
        delete_response: bool = ...,
    ) -> Message: ...

    @no_type_check
    async def send(
        self,
        content: Optional[str] = None,
        *,
        tts: bool = False,
        embed: Optional[Embed] = None,
        embeds: Optional[Sequence[Embed]] = None,
        file: Optional[File] = None,
        files: Optional[Sequence[File]] = None,
        stickers: Optional[Sequence[Union[GuildSticker, StickerItem]]] = None,
        delete_after: Optional[float] = None,
        nonce: Optional[Union[str, int]] = None,
        allowed_mentions: Optional[AllowedMentions] = None,
        reference: Optional[Union[Message, MessageReference, PartialMessage]] = None,
        mention_author: Optional[bool] = None,
        view: Optional[View] = None,
        suppress_embeds: bool = False,
        ephemeral: bool = False,
        silent: bool = False,
        poll: Optional[Poll] = None,
        edit_response: bool = False,
        delete_response: bool = False,
    ) -> Union[Message, WebhookMessage]:
        if reference and self.author.system:
            reference = None

        if edit_response and self.response:
            return await self.response.edit(
                content=content or MISSING,
                embed=embed or MISSING,
                embeds=embeds or MISSING,
                attachments=files or [file] if file else MISSING,
                view=view or MISSING,
            )

        elif delete_response and self.response:
            await quietly_delete(self.response)

        for _embed in embeds or [embed]:
            if _embed and getattr(_embed, "dominant_color", False):
                await _embed.wait_for_dominant_color()

        if self.reskin and self.reskin.status:
            webhook = await self.reskin_webhook()
            if webhook:
                self.response = await webhook.send(
                    content=content or "",
                    username=self.reskin.username,
                    avatar_url=self.reskin.avatar_url,
                    tts=tts,
                    ephemeral=ephemeral,
                    file=file or MISSING,
                    files=files or MISSING,
                    embed=embed or MISSING,
                    embeds=embeds or MISSING,
                    allowed_mentions=allowed_mentions or MISSING,
                    view=view or MISSING,
                    thread=(
                        self.channel if isinstance(self.channel, Thread) else MISSING
                    ),
                    wait=True,
                    suppress_embeds=suppress_embeds,
                    silent=silent,
                )
                if delete_after:
                    await self.response.delete(delay=delete_after)

                return self.response

        try:
            self.response = await super().send(
                content=content,
                tts=tts,
                embed=embed,
                embeds=embeds,
                file=file,
                files=files,
                stickers=stickers,
                delete_after=delete_after,
                nonce=nonce,
                allowed_mentions=allowed_mentions,
                reference=reference,
                mention_author=mention_author,
                view=view,
                suppress_embeds=suppress_embeds,
                ephemeral=ephemeral,
                silent=silent,
                poll=poll,
            )
        except HTTPException as exc:
            if exc.code == 50035 and "Unknown message" in exc.text:
                self.response = await super().send(
                    content=content,
                    tts=tts,
                    embed=embed,
                    embeds=embeds,
                    file=file,
                    files=files,
                    stickers=stickers,
                    delete_after=delete_after,
                    nonce=nonce,
                    allowed_mentions=allowed_mentions,
                    mention_author=mention_author,
                    view=view,
                    suppress_embeds=suppress_embeds,
                    ephemeral=ephemeral,
                    silent=silent,
                    poll=poll,
                )
            else:
                raise

        assert isinstance(self.response, Message)
        return self.response

    async def respond(self, *args: str, **kwargs) -> Message:
        embed = Embed(description="\n".join(args), color=kwargs.pop("color", None))
        return await self.send(
            embed=embed, reference=kwargs.pop("reference", self.message), **kwargs
        )

    async def approve(self, *args: str, **kwargs) -> Message:
        embed = Embed(description="\n".join(args), color=kwargs.pop("color", None))
        return await self.send(
            embed=embed, reference=kwargs.pop("reference", self.message), **kwargs
        )

    async def warn(self, *args: str, **kwargs) -> Message:
        embed = Embed(description="\n".join(args), color=kwargs.pop("color", None))
        return await self.send(
            embed=embed, reference=kwargs.pop("reference", self.message), **kwargs
        )

    async def prompt(self, *args: str, **kwargs) -> Literal[True]:
        key = f"prompt:{self.author.id}:{self.command.qualified_name}"
        async with self.bot.redis.get_lock(key):
            embed = Embed(description="\n".join(args), color=kwargs.pop("color", None))
            confirmation = Confirmation(self)

            try:
                message = await self.send(
                    embed=embed,
                    reference=kwargs.pop("reference", self.message),
                    view=confirmation,
                    **kwargs,
                )
            except discord.HTTPException:
                raise commands.UserInputError("Prompt send failure")

            await confirmation.wait()
            await quietly_delete(message)

            if not confirmation.status:
                raise commands.UserInputError("Confirmation prompt wasn't accepted")

            return confirmation.status

    async def choose_option(self, options: list[Any]) -> Any:
        try:
            message = await self.bot.wait_for(
                "message",
                check=lambda message: (
                    message.content
                    and message.content.isdigit()
                    and int(message.content) in range(1, len(options) + 1)
                    and message.author == self.author
                    and message.channel == self.channel
                ),
            )
        except asyncio.TimeoutError:
            raise commands.CommandError("You took too long to respond")
        else:
            await quietly_delete(message)
            return options[int(message.content) - 1]

    async def add_check(self) -> None:
        return await self.message.add_reaction("✅")


class Embed(discord.Embed):
    dominant_color: bool = False
    _dominant_color_task: Optional[asyncio.Task] = None

    def __init__(self, **kwargs) -> None:
        self.dominant_color = kwargs.pop("dominant_color", False)
        super().__init__(**kwargs)
        self.color = self._resolve_color()

    def _resolve_color(self) -> discord.Color:
        if self.color is not None:
            return self.color

        ctx = cast(Optional[Context], from_stack("ctx"))
        if ctx and ctx.reskin:
            if ctx.reskin.embed_color.value == 1337:
                return discord.Color.random(seed=ctx.id)
            return discord.Color(ctx.reskin.embed_color.value)

        return discord.Color.dark_embed()

    async def set_dominant_color(self, url: str) -> None:
        async with ClientSession() as session:
            async with session.get(str(url)) as response:
                if not response.ok:
                    return

                buffer = await response.read()

        self.color = await dominant_color(buffer)

    async def wait_for_dominant_color(self) -> None:
        if self._dominant_color_task:
            await self._dominant_color_task

    def set_image(self, url: str) -> Self:
        if self.dominant_color:
            self._dominant_color_task = asyncio.create_task(
                self.set_dominant_color(url)
            )

        return super().set_image(url=url)

    def set_thumbnail(self, *, url: str) -> Self:
        if self.dominant_color:
            self._dominant_color_task = asyncio.create_task(
                self.set_dominant_color(url)
            )

        return super().set_thumbnail(url=url)

    def add_field(self, *, name: Any, value: Any, inline: bool = True) -> Self:
        return super().add_field(name=f"**{name}**", value=value, inline=inline)


discord.Embed = Embed
