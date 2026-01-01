from __future__ import annotations

import random
from typing import TYPE_CHECKING, Any

from discord import Guild, Member, Role, TextChannel, Thread
from discord.utils import snowflake_time

from . import Adapter

if TYPE_CHECKING:
    from discord.object import Object


__all__ = (
    "DiscordAdapter",
    "GuildAdapter",
    "MemberAdapter",
    "ChannelAdapter",
    "RoleAdapter",
)


class DiscordAdapter(Adapter):
    """
    The base class for all Discord adapter blocks.
    """

    object: Object | Any

    def __init__(self, base: Object | Any):
        super().__init__()
        self.object = base
        created_at = getattr(base, "created_at", snowflake_time(base.id))
        self.objects.update(
            {
                "id": base.id,
                "name": getattr(base, "name", base.id),
                "mention": getattr(base, "mention", base.id),
                "created_at": created_at,
                "created_at_timestamp": int(created_at.timestamp()),
            }
        )


class GuildAdapter(DiscordAdapter):
    """
    The base class for a Discord guild.
    """

    object: Guild

    def __init__(self, guild: Guild):
        super().__init__(guild)
        self.objects.update(
            {
                "icon": guild.icon,
                "icon_url": guild.icon,
                "banner": guild.banner,
                "banner_url": guild.banner,
                "splash": guild.splash,
                "splash_url": guild.splash,
                "description": guild.description,
                "owner": MemberAdapter(guild.owner) if guild.owner else None,
                "roles": ", ".join(role.mention for role in reversed(guild.roles)),
                "role_ids": ", ".join(str(role.id) for role in reversed(guild.roles)),
                "text_channels": ", ".join(
                    channel.mention for channel in guild.text_channels
                ),
                "voice_channels": ", ".join(
                    channel.mention for channel in guild.voice_channels
                ),
                "category_channels": ", ".join(
                    channel.mention for channel in guild.categories
                ),
                "text_channel_count": len(guild.text_channels),
                "voice_channel_count": len(guild.voice_channels),
                "category_channel_count": len(guild.categories),
                "member_count": guild.member_count,
                "emoji_count": len(guild.emojis),
                "role_count": len(guild.roles),
                "member": self.random_member,
            }
        )

    def random_member(self) -> MemberAdapter:
        """
        Returns a random member from the guild.
        """

        member = random.choice(self.object.members)
        return MemberAdapter(member)


class MemberAdapter(DiscordAdapter):
    """
    The base class for a Discord member.
    """

    def __init__(self, member: Member):
        super().__init__(member)
        joined_at = member.joined_at or snowflake_time(member.id)
        self.objects.update(
            {
                "color": member.color,
                "colour": member.colour,
                "nick": member.display_name,
                "display_name": member.display_name,
                "avatar": member.display_avatar.url,
                "avatar_url": member.display_avatar.url,
                "booster": bool(member.premium_since),
                "booster_since": member.premium_since,
                "booster_since_timestamp": int(
                    member.premium_since.timestamp() if member.premium_since else 0
                ),
                "joined_at": joined_at,
                "joined_at_timestamp": int(joined_at.timestamp()),
                "top_role": RoleAdapter(member.top_role),
                "roles": ", ".join(role.mention for role in reversed(member.roles[1:])),
                "role_ids": ", ".join(
                    str(role.id) for role in reversed(member.roles[1:])
                ),
            }
        )


class ChannelAdapter(DiscordAdapter):
    """
    The base class for a Discord channel.
    """

    def __init__(self, channel: TextChannel | Thread):
        super().__init__(channel)
        self.objects["type"] = channel.type.name
        if isinstance(channel, TextChannel):
            self.objects.update(
                {
                    "topic": channel.topic,
                    "nsfw": channel.is_nsfw(),
                    "position": channel.position,
                    "slowmode": channel.slowmode_delay,
                    "slowmode_delay": channel.slowmode_delay,
                }
            )


class RoleAdapter(DiscordAdapter):
    """
    The base class for a Discord role.
    """

    def __init__(self, role: Role):
        super().__init__(role)
        self.objects.update(
            {
                "color": role.color,
                "colour": role.colour,
                "position": role.position,
                "hoist": role.hoist,
                "mentionable": role.mentionable,
                "managed": role.managed,
            }
        )
