from __future__ import annotations

import base64
import json
import re
from contextlib import suppress
from typing import TYPE_CHECKING, List, Literal, Optional, Tuple, TypedDict, cast

from discord import (
    ActionRow,
    ButtonStyle,
    Color,
    Embed,
    Guild,
    GuildSticker,
    Member,
    Message,
    StandardSticker,
    TextChannel,
    Thread,
    VoiceChannel,
    Webhook,
)
from discord.ui import Button, View
from discord.utils import find

from .engine.embed import EmbedBuilder
from .engine.node import Node
from .variables import Block, parse

if TYPE_CHECKING:
    from bot.core import Context, Juno


class ScriptData(TypedDict):
    content: Optional[str]
    embeds: List[Embed]
    view: View
    stickers: List[GuildSticker | StandardSticker]


class Script:
    template: str
    blocks: List[Block | Tuple[str, Block]]
    nodes: List[Node]

    def __init__(
        self,
        template: str,
        blocks: List[Block | Tuple[str, Block]] = [],
    ) -> None:
        self.template = template
        self.blocks = blocks
        self.nodes = Node.find(self.fixed_template)

    def __repr__(self) -> str:
        return f"<Script nodes={len(self.nodes)} {self.template=}>"

    def __str__(self) -> str:
        return self.template

    def __bool__(self) -> bool:
        return bool(self.content or self.embeds or self.view.children or self.stickers)
    
    @property
    def fixed_template(self) -> str:
        return parse(self.template, self.blocks)

    @property
    def content(self) -> Optional[str]:
        return self.data["content"]

    @property
    def embeds(self) -> List[Embed]:
        return self.data["embeds"][:10]

    @property
    def view(self) -> View:
        return self.data["view"]

    @property
    def stickers(self) -> List[GuildSticker | StandardSticker]:
        return self.data["stickers"]

    @property
    def format(self) -> Literal["text", "embed", "sticker"]:
        if self.stickers and not self.content and not self.embeds:
            return "sticker"

        return "text" if not self.data["embeds"] else "embed"

    @classmethod
    async def convert(cls, ctx: Context, argument: str) -> Script:
        script = cls(argument, [ctx.guild, ctx.channel, ctx.author])
        if not script:
            raise ValueError("Script nodes were not provided")

        return script

    @classmethod
    def from_message(cls, message: Message) -> Script:
        template: List[str] = []
        if message.system_content:
            template.append(f"{{content: {message.system_content}}}")

        for embed in message.embeds:
            template.append("{embed}")
            template.extend(
                f"{{{item}: {getattr(value, 'url', value)}}}"
                for item, value in (
                    ("color", embed.color),
                    ("url", embed.url),
                    ("title", embed.title),
                    ("description", embed.description),
                    ("thumbnail", embed.thumbnail),
                    ("image", embed.image),
                )
                if value
            )

            for field in embed.fields:
                _field: List[str] = [field.name, field.value]  # type: ignore
                if field.inline:
                    _field.append("inline")

                template.append(f"{{field: {' && '.join(_field)}}}")

            if (footer := embed.footer) and footer.text:
                _footer: List[str] = [footer.text]
                if footer.icon_url:
                    _footer.append(footer.icon_url)

                template.append(f"{{footer: {' && '.join(_footer)}}}")

            if (author := embed.author) and author.name:
                _author: List[str] = [
                    author.name,
                    author.url or "null",
                ]
                if author.icon_url:
                    _author.append(author.icon_url)

                template.append(f"{{author: {' && '.join(_author)}}}")

        if message.components:
            for component in message.components:
                if not isinstance(component, ActionRow):
                    continue

                for child in component.children:
                    if not isinstance(child, Button):
                        continue

                    elif child.style != ButtonStyle.link:
                        continue

                    label = child.label
                    url = child.url
                    emoji = child.emoji

                    template.append(f"{{button: {label} && {url} && {emoji}}}")

        return cls("\n".join(template), [])

    def from_discohook(self) -> Optional[dict]:
        if not (match := re.search(r"\?data=(.+)", self.fixed_template)):
            return None

        encoded = match.group(1)
        padding = 4 - len(encoded) % 4
        if padding != 4:
            encoded += "=" * padding

        try:
            decoded = base64.urlsafe_b64decode(encoded).decode("utf-8")
            return json.loads(parse(decoded, self.blocks))["messages"][0]["data"]
        except (json.JSONDecodeError, KeyError):
            return None

    @property
    def parsed_json(self) -> Optional[ScriptData]:
        data: ScriptData = {
            "content": None,
            "embeds": [],
            "view": View(),
            "stickers": [],
        }

        try:
            script = json.loads(self.fixed_template)
        except json.JSONDecodeError:
            if discohook := self.from_discohook():
                script = discohook
            else:
                return None

        if not isinstance(script, dict):
            return None

        data["content"] = script.get("content")
        for embed in script.get("embeds", []):
            embed["color"] = embed.get("color") or 0
            data["embeds"].append(Embed.from_dict(embed))

        return data

    @property
    def data(self) -> ScriptData:
        if parsed := self.parsed_json:
            return parsed

        data: ScriptData = {
            "content": None,
            "embeds": [],
            "view": View(),
            "stickers": [],
        }

        for node in self.nodes:
            if node.name in ("content", "message", "msg"):
                data["content"] = node.value

            elif node.name == "button":
                parts = node.value.split("&&", 3)
                if len(parts) < 2:
                    continue

                label = parts[0].strip()
                url = parts[1].strip()
                emoji = parts[2].strip() if len(parts) == 3 else None

                if label.startswith("<"):  # custom emoji
                    label, emoji = None, label

                with suppress(ValueError):
                    data["view"].add_item(
                        Button(
                            label=label,
                            url=url,
                            emoji=emoji,
                        )
                    )

            elif node.name == "sticker":
                guild = cast(
                    Optional[Guild],
                    find(lambda g: isinstance(g, Guild), self.blocks),
                )
                if not guild:
                    continue

                bot = cast("Juno", guild._state._get_client())
                sticker = find(
                    lambda s: s.name.lower() == node.value.lower(),
                    list(guild.stickers) + list(bot.wumpus_stickers),
                )
                if sticker:
                    data["stickers"].append(sticker)

            else:
                if node.name == "embed":
                    data["embeds"].append(Embed(color=Color.dark_embed()))

                elif not data["embeds"]:
                    data["embeds"].append(Embed(color=Color.dark_embed()))

                embed = data["embeds"][-1]
                EmbedBuilder(embed)(node)

        if not any(data.get(key) for key in ("content", "embeds")) and not (
            data["view"].children or data["stickers"]
        ):
            data["content"] = self.fixed_template

        for embed in data["embeds"]:
            if not embed:
                data["embeds"].remove(embed)

        return data

    async def send(
        self,
        channel: Context | VoiceChannel | TextChannel | Thread | Webhook | Member,
        **kwargs,
    ) -> Message:
        if not isinstance(channel, Webhook):
            kwargs["stickers"] = self.stickers
            
        return await channel.send(
            content=self.content,  # type: ignore
            embeds=self.embeds,
            view=self.view,
            **kwargs,
        )

    async def edit(
        self,
        message: Message,
        **kwargs,
    ) -> Message:
        webhook: Optional[Webhook] = kwargs.pop("webhook", None)
        if webhook:
            return await webhook.edit_message(
                message.id,
                content=self.content,
                embeds=self.embeds,
                **kwargs,
            )

        return await message.edit(
            content=self.content,
            embeds=self.embeds,
            view=self.view,
            **kwargs,
        )
