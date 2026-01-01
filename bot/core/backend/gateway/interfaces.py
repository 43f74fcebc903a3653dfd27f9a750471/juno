from datetime import datetime
from typing import List, Optional

import discord
from discord.abc import GuildChannel
from discord.utils import utcnow
from pydantic import BaseModel


class PartialRole(BaseModel):
    id: int
    name: str
    color: int
    permissions: int
    icon_url: Optional[str]
    managed: bool
    mentionable: bool
    unicode_emoji: Optional[str]
    default_role: bool

    @classmethod
    def parse(cls, role: discord.Role):
        return cls(
            id=role.id,
            name=role.name,
            color=role.color.value,
            permissions=role.permissions.value,
            icon_url=role.icon.url if role.icon else None,
            managed=role.managed,
            mentionable=role.mentionable,
            unicode_emoji=role.unicode_emoji,
            default_role=role.is_default(),
        )


class PartialChannel(BaseModel):
    id: int
    name: str
    type: str
    position: int
    nsfw: bool
    parent_id: Optional[int]
    permission_overwrites: dict[int, dict[str, bool]]

    @classmethod
    def parse(cls, channel: GuildChannel):
        return cls(
            id=channel.id,
            name=channel.name,
            type=channel.type.name,
            position=channel.position,
            nsfw=getattr(channel, "is_nsfw", lambda: False)(),
            parent_id=channel.category_id,
            permission_overwrites={},
        )


class PartialMember(BaseModel):
    id: int
    bot: bool
    username: str
    display_name: str
    avatar_url: str
    roles: List[int]
    joined_at: datetime
    created_at: datetime
    permissions: int

    @classmethod
    def parse(cls, member: discord.Member):
        return cls(
            id=member.id,
            bot=member.bot,
            username=member.name,
            display_name=member.display_name,
            avatar_url=member.display_avatar.url,
            roles=[role.id for role in member.roles],
            joined_at=member.joined_at or utcnow(),
            created_at=member.created_at,
            permissions=member.guild_permissions.value,
        )


class PartialGuild(BaseModel):
    id: int
    name: str
    owner_id: int
    created_at: datetime
    icon_url: Optional[str]
    roles: List[PartialRole]
    channels: List[PartialChannel]

    @classmethod
    def parse(cls, guild: discord.Guild):
        return cls(
            id=guild.id,
            name=guild.name,
            owner_id=guild.owner_id or 0,
            created_at=guild.created_at,
            icon_url=guild.icon.url if guild.icon else None,
            roles=[PartialRole.parse(role) for role in guild.roles],
            channels=[PartialChannel.parse(channel) for channel in guild.channels],
        )
