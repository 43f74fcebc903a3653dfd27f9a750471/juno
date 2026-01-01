from io import BytesIO
from typing import Literal, Optional
from aiohttp import FormData
from discord import File, Message
from discord.ext.commands import Cog, command, cooldown, BucketType, parameter, flag
from xxhash import xxh64_hexdigest
from bot.core import Juno, Context
from bot.shared.converters import FlagConverter, Status
from bot.shared.converters.attachment import PartialAttachment
from yarl import URL

API_URL = URL.build(
    scheme="https",
    host="dev.rawr.software",
    path="/v1/flux",
)


class CaptionFlags(FlagConverter):
    black: Status = flag(
        description="Set the caption background to black.",
        default=False,
    )
    bottom: Status = flag(
        description="Place the caption at the bottom of the image.",
        default=False,
    )


async def flux(
    ctx: Context,
    operation: Literal["caption", "speech-bubble", "flag2", "april-fools"],
    attachment: PartialAttachment,
    **payload,
) -> File:
    data = FormData()
    data.add_field("media", attachment.buffer, content_type=attachment.content_type)
    for key, value in payload.items():
        if isinstance(value, bool):
            value = str(value).lower()

        data.add_field(key, value)

    async with ctx.bot.session.post(
        API_URL / operation,
        data=data,
    ) as response:
        buffer = await response.read()
        if not buffer:
            raise ValueError("The API didn't return a valid response - try again later")

        name = xxh64_hexdigest(buffer)
        extension = "gif" if operation != "april-fools" else "mp4"
        return File(
            BytesIO(buffer),
            filename=f"juno{operation.upper()}{name}FLUX.{extension}",
        )


class Media(Cog):
    def __init__(self, bot: Juno) -> None:
        self.bot = bot

    @command(name="caption", aliases=("cap",), extras={"flags": CaptionFlags})
    @cooldown(1, 3, BucketType.user)
    async def caption(
        self,
        ctx: Context,
        attachment: Optional[PartialAttachment] = parameter(
            default=lambda ctx: PartialAttachment.fallback(ctx, ("image",)),
        ),
        *,
        text: str,
    ) -> Message:
        """Place a caption on an image."""

        if not attachment:
            return await ctx.send_help(ctx.command)

        elif attachment.format != "image":
            return await ctx.warn("The attachment provided isn't an image")

        text, flags = await CaptionFlags().find(ctx, text)
        if not text:
            return await ctx.send_help(ctx.command)

        async with ctx.typing():
            file = await flux(
                ctx,
                "caption",
                attachment,
                text=text,
                black=flags.black,
                bottom=flags.bottom,
            )
            return await ctx.reply(file=file)

    @command(name="speech", aliases=("speechbubble", "bubble"))
    @cooldown(1, 3, BucketType.user)
    async def speech(
        self,
        ctx: Context,
        attachment: PartialAttachment = parameter(
            default=lambda ctx: PartialAttachment.fallback(ctx, ("image",)),
        ),
    ) -> Message:
        """Place a speech bubble on an image."""

        if attachment.format != "image":
            return await ctx.warn("The attachment provided isn't an image")

        async with ctx.typing():
            file = await flux(ctx, "speech-bubble", attachment)
            return await ctx.reply(file=file)

    @command(name="flag", aliases=("flagpole",))
    @cooldown(1, 3, BucketType.user)
    async def flag(
        self,
        ctx: Context,
        attachment: PartialAttachment = parameter(
            default=lambda ctx: PartialAttachment.fallback(ctx, ("image",)),
        ),
    ) -> Message:
        """Place an image on a flag."""

        if attachment.format != "image":
            return await ctx.warn("The attachment provided isn't an image")

        async with ctx.typing():
            file = await flux(ctx, "flag2", attachment)
            return await ctx.reply(file=file)

    @command(name="aprilfools", aliases=("april", "fools", "af"))
    @cooldown(1, 3, BucketType.user)
    async def april_fools(
        self,
        ctx: Context,
        attachment: PartialAttachment = parameter(
            default=lambda ctx: PartialAttachment.fallback(ctx, ("image",)),
        ),
    ) -> Message:
        """Place an image on an April Fools' slideshow."""

        if attachment.format != "image":
            return await ctx.warn("The attachment provided isn't an image")

        async with ctx.typing():
            file = await flux(ctx, "april-fools", attachment)
            return await ctx.reply(file=file)
