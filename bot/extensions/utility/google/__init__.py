from logging import getLogger
from typing import Annotated, List, Optional

from discord import Embed, Message
from discord.ext.commands import (
    BucketType,
    Cog,
    clean_content,
    command,
    cooldown,
    flag,
    group,
    has_permissions,
    max_concurrency,
    parameter,
)
from discord.utils import escape_markdown
from humanize import intword
from yarl import URL

from bot.core import Context, Juno
from bot.shared import Paginator, codeblock
from bot.shared.converters import FlagConverter, PartialAttachment
from bot.shared.formatter import shorten
from bot.shared.paginator import EmbedField

from .extraction import GoogleImage, GoogleReverse, GoogleSearch, GoogleTranslate

logger = getLogger("bot.google")

BASE_URL = URL.build(
    scheme="https",
    host="www.google.com",
    path="/search",
)


class GoogleSearchFlags(FlagConverter):
    locale: str = flag(
        description="The locale language to search in",
        default="en",
    )


class Google(Cog):
    def __init__(self, bot: Juno) -> None:
        self.bot = bot

    @group(aliases=("search", "g"), invoke_without_command=True)
    @max_concurrency(1, BucketType.user)
    @cooldown(4, 60, BucketType.channel)
    async def google(self, ctx: Context, *, query: str) -> Message:
        """Search Google for a provided query."""

        query, flags = await GoogleSearchFlags().find(ctx, query)
        async with ctx.typing():
            data = await GoogleSearch.search(
                query,
                safe=not ctx.channel.is_nsfw() and ctx.settings.google_safe_search,
                locale=flags.locale,
            )
            if not data.results:
                return await ctx.warn("No results were found for the provided query")

        fields: List[EmbedField] = []
        embed = Embed(title=f"Google Search: {query}")
        embed.set_footer(
            text="Page {{page}}/{{pages}} of {total_results} Google Search results".format(
                total_results=intword(data.total_results, format="%.0f"),
            ),
            icon_url="https://i.imgur.com/2pbcz3S.png",
        )
        if data.header:
            embed.title = data.header + (
                f" - {data.description}" if data.description else ""
            )

        if panel := data.panel:
            if panel.source:
                embed.url = panel.source.url

            embed.description = shorten(panel.description, 200)
            for item in panel.items:
                if not embed.description:
                    embed.description = ""

                embed.description += f"\n> **{item.name}:** {item.hyperlink}"

        if card := data.rich_card:
            if not embed.description:
                embed.description = ""

            embed.description += f"\n{card.formatted}"

        for result in data.results:
            if any(result.title in field["name"] for field in fields):
                continue

            description = result.description or (".." if not result.tweets else "")
            fields.append(
                {
                    "name": result.title,
                    "value": (
                        f"[**{result.cite}**]({result.url})\n{shorten(description, 200)}"
                        + (
                            "\n"
                            if result.extended_links or result.tweets and description
                            else ""
                        )
                        + "\n".join(
                            [
                                f"> [`{shorten(extended.title, 18)}`]({extended.url}): {shorten(extended.snippet or '...', 46)}"
                                for extended in result.extended_links
                            ]
                        )
                        + "\n".join(
                            [
                                f"> [`{shorten(escape_markdown(tweet.text), 37)}`]({tweet.url}) **{tweet.footer}**"
                                for tweet in result.tweets[:3]
                            ]
                        )[:1024]
                    ),
                    "inline": False,
                }
            )

        paginator = Paginator(ctx, fields, embed, per_page=3)
        return await paginator.start()

    @google.command(name="safety", aliases=("safe", "nsfw"))
    @has_permissions(manage_channels=True)
    async def google_safety(self, ctx: Context) -> Message:
        """Toggle Google Safe Search for the server."""

        status = not ctx.settings.google_safe_search
        await ctx.settings.upsert(google_safe_search=status)
        return await ctx.approve(
            f"Google Safe Search is now {'enabled' if status else 'disabled'}"
        )

    @google.command(name="image", aliases=("images", "img", "im", "i"))
    @max_concurrency(1, BucketType.user)
    @cooldown(6, 40, BucketType.channel)
    async def google_image(self, ctx: Context, *, query: str) -> Message:
        """Search for images on Google."""

        async with ctx.typing():
            results = await GoogleImage.search(
                query=query,
                safe=not ctx.channel.is_nsfw() and ctx.settings.google_safe_search,
            )
            if not results:
                return await ctx.warn("No results were found for the provided query")

        embeds: List[Embed] = []
        for result in results:
            embed = Embed(
                url=result.url,
                title=f"{result.title} ({result.domain})",
                description=result.description,
            )
            embed.set_image(
                url=URL.build(
                    scheme="https",
                    host="proxy-na1.bleed.bot",
                    path="/google",
                    query={
                        "url": result.image.url,
                        "fallback": result.thumbnail.url,
                    },
                )
            )
            embed.set_footer(
                text="Page {page}/{pages} of Google Images",
                icon_url="https://i.imgur.com/2pbcz3S.png",
            )
            embeds.append(embed)

        paginator = Paginator(ctx, embeds)
        return await paginator.start()

    @google.command(name="reverse", aliases=("sauce", "rimage", "rimg", "r"))
    @max_concurrency(1, BucketType.user)
    @cooldown(3, 30, BucketType.channel)
    async def google_reverse(
        self,
        ctx: Context,
        attachment: PartialAttachment = parameter(
            default=lambda ctx: PartialAttachment.fallback(ctx, ("image",)),
        ),
    ) -> Message:
        """Reverse search an image on Google."""

        if attachment.format != "image":
            return await ctx.warn("The file must be an image")

        async with ctx.typing():
            data = await GoogleReverse.search(
                self.bot,
                image_url=attachment.url,
                safe=not ctx.channel.is_nsfw(),
            )
            if not data.results:
                return await ctx.warn("No results were found for the provided image")

        fields: List[EmbedField] = []
        embed = Embed(title="Google Reverse Image Search")
        embed.set_thumbnail(url=attachment.url)
        embed.set_footer(
            text=data.statistics,
            icon_url="https://i.imgur.com/2pbcz3S.png",
        )

        if data.related:
            embed.description = (
                f"> Possible related search: [*`{data.related}`*]({data.related_url})"
            )

        for result in data.results:
            fields.append(
                {
                    "name": result.title,
                    "value": "\n".join(
                        [result.pretty_url, shorten(result.description, 200)]
                    ),
                    "inline": False,
                }
            )

        paginator = Paginator(ctx, fields, embed, per_page=3)
        return await paginator.start()

    @google.command(name="translate", aliases=("translation", "tr", "t"))
    @max_concurrency(1, BucketType.user)
    async def google_translate(
        self,
        ctx: Context,
        destination: Annotated[str, Optional[GoogleTranslate]] = "en",
        *,
        text: Annotated[Optional[str], clean_content] = None,
    ) -> Message:
        """Translate text to a specified language."""

        if not text:
            reply = ctx.replied_message
            if not reply or not reply.content:
                return await ctx.warn("You must provide text to translate")

            text = reply.clean_content

        async with ctx.typing():
            result = await GoogleTranslate.translate(
                self.bot,
                query=text,
                target=destination,
            )

        embed = Embed(title="Google Translate")
        embed.add_field(
            name=f"{result.original.language} to {result.translated.language}",
            value=(
                f"[*{result.original.speech}*]({result.translated.details})"
                if result.original.speech
                else ""
            )
            + "\n>>> "
            + codeblock(result.translated.text),
        )

        return await ctx.send(embed=embed)

    @command(name="image", aliases=("images", "img", "im", "i"))
    @max_concurrency(1, BucketType.user)
    @cooldown(6, 40, BucketType.channel)
    async def image(self, ctx: Context, *, query: str) -> Message:
        """Search for images on Google."""

        return await self.google_image(ctx, query=query)

    @command(aliases=("sauce", "rimage", "rimg"))
    @max_concurrency(1, BucketType.user)
    @cooldown(3, 30, BucketType.channel)
    async def reverseimage(
        self,
        ctx: Context,
        attachment: PartialAttachment = parameter(
            default=lambda ctx: PartialAttachment.fallback(ctx, ("image",)),
        ),
    ) -> Message:
        """Reverse search an image on Google."""

        return await self.google_reverse(ctx, attachment=attachment)

    @command(aliases=("translation", "tr", "t"))
    @max_concurrency(1, BucketType.user)
    async def translate(
        self,
        ctx: Context,
        destination: Annotated[str, Optional[GoogleTranslate]] = "en",
        *,
        text: Annotated[Optional[str], clean_content] = None,
    ) -> Message:
        """Translate text to a specified language."""

        return await self.google_translate(ctx, destination=destination, text=text)
