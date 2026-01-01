import re
from contextlib import suppress
from socket import AF_INET
from typing import Literal, Optional, Self, TypeVar, cast

from aiohttp import ClientSession, TCPConnector
from aiohttp_proxy import ProxyConnector
from discord import Asset, Forbidden, HTTPException, Message, NotFound
from discord.ext.commands import (
    BadArgument,
    CommandError,
    MemberConverter,
    MessageConverter,
)

from bot.core import Context
from config import config

__all__ = ("PartialAttachment", "EXTENSION_MAP")

EXTENSION_MAP = {
    "image": ("png", "jpg", "jpeg", "webp", "gif"),
    "video": ("mp4", "webm", "mov"),
    "audio": ("mp3", "wav", "ogg", "flac", "opus"),
    "application": ("zip", "rar", "7z", "tar", "gz", "xz", "bz2", "x-msdos-program"),
}
FormatType = Literal["audio", "video", "image", "application"]
T = TypeVar("T", bound=FormatType)


class PartialAttachment:
    url: str
    buffer: bytes
    content_type: str
    filename: str

    def __init__(
        self,
        url: str,
        buffer: bytes,
        content_type: str,
        filename: Optional[str] = None,
    ) -> None:
        self.url = url
        self.buffer = buffer
        self.content_type = content_type
        self.filename = filename or url.split("/")[-1].split("?")[0]

    def __str__(self) -> str:
        return self.filename

    @property
    def format(self) -> str:
        return self.content_type.split("/")[0]

    @property
    def extension(self) -> str:
        return self.content_type.split("/")[1]

    async def read(self) -> bytes:
        return self.buffer

    @staticmethod
    async def fetch(url: Asset | str, proxy: bool = False) -> tuple[str, bytes]:
        if isinstance(url, Asset):
            url = str(url)

        async with ClientSession(
            connector=ProxyConnector.from_url(config.http_proxy)
            if config.http_proxy and False
            else TCPConnector(family=AF_INET),
        ) as session:
            async with session.get(url) as response:
                if (size := response.content_length) and size > (50 * 1024 * 1024):
                    raise CommandError("File exceeds the maximum size of 50MB")

                elif response.ok:
                    buffer = await response.read()
                    return response.content_type, buffer

                elif response.status == 404:
                    raise NotFound(response, "asset not found")

                elif response.status == 403:
                    raise Forbidden(response, "cannot retrieve asset")

                else:
                    raise HTTPException(response, "failed to retrieve asset")

    @staticmethod
    def _validate_format(
        content_type: str,
        allowed_formats: tuple[FormatType, ...],
    ) -> bool:
        """Validate if the content type matches any of the allowed formats."""
        if not content_type or not allowed_formats:
            return True

        return any(fmt in content_type.lower() for fmt in allowed_formats)

    @classmethod
    def get_attachment(
        cls,
        message: Message,
        allowed_formats: tuple[FormatType, ...] = (),
    ) -> tuple[Optional[str], Optional[str], bool]:
        """Get the first attachment URL from a message."""

        if message.attachments:
            attachment = message.attachments[0]
            if not cls._validate_format(attachment.content_type or "", allowed_formats):
                return None, None, False

            return message.attachments[0].url, attachment.filename, False

        elif message.stickers:
            sticker = message.stickers[0]
            if not cls._validate_format("image", allowed_formats):
                return None, None, False

            return message.stickers[0].url, sticker.name, False

        elif message.embeds:
            for embed in message.embeds:
                if embed.image and cls._validate_format("image", allowed_formats):
                    return cast(str, embed.image.url), None, True

                elif embed.thumbnail and cls._validate_format("image", allowed_formats):
                    return cast(str, embed.thumbnail.url), None, True

        return None, None, False

    @classmethod
    async def convert(
        cls,
        ctx: Context,
        argument: str,
        allowed_formats: tuple[FormatType, ...] = (),
    ) -> Self:
        if re.match(r"<@!?(\d+)>", argument) and cls._validate_format(
            "image", allowed_formats
        ):
            with suppress(CommandError):
                member = await MemberConverter().convert(ctx, argument)
                content_type, buffer = await cls.fetch(member.display_avatar)
                return cls(member.display_avatar.url, buffer, content_type)

        elif re.match(r"https?://", argument):
            with suppress(CommandError):
                content_type, buffer = await cls.fetch(argument, proxy=True)
                if not cls._validate_format(content_type, allowed_formats):
                    raise BadArgument("The provided URL is not in a supported format")

                return cls(argument, buffer, content_type)

        with suppress(CommandError):
            message = await MessageConverter().convert(ctx, argument)
            url, filename, proxy = cls.get_attachment(message)
            if url:
                content_type, buffer = await cls.fetch(url, proxy)
                return cls(url, buffer, content_type, filename)

        raise BadArgument("No file found in the message")

    @classmethod
    async def fallback(
        cls,
        ctx: Context,
        allowed_formats: tuple[FormatType, ...] = (),
    ) -> Self:
        message = ctx.replied_message or ctx.message
        url, filename, proxy = cls.get_attachment(message)
        if not url:
            async for message in ctx.channel.history():
                url, filename, proxy = cls.get_attachment(message, allowed_formats)
                if url:
                    break

        if not url:
            raise BadArgument("You must provide an attachment")

        content_type, buffer = await cls.fetch(url, proxy)
        return cls(url, buffer, content_type, filename)
