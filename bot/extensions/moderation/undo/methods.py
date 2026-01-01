from typing import Any, Callable, Coroutine, Dict, Protocol, runtime_checkable

from discord import Asset, AuditLogAction, AuditLogEntry, HTTPException, Object
from discord.abc import Snowflake


@runtime_checkable
class Deletable(Protocol):
    async def delete(self) -> None: ...


@runtime_checkable
class Editable(Protocol):
    async def edit(self, *args, **kwargs) -> None: ...


REASON = "Reverted via the undo command"


async def delete_method(entry: AuditLogEntry):
    target = entry.target
    if not isinstance(target, Deletable):
        return

    return await target.delete()


async def edit_method(entry: AuditLogEntry):
    target = entry.target
    if not isinstance(target, Editable):  # type: ignore
        return

    kwargs = dict(entry.changes.after)
    for key, value in kwargs.copy().items():
        if isinstance(value, Asset):
            try:
                kwargs[key] = await value.read()
            except HTTPException:
                del kwargs[key]

        elif isinstance(value, Object):
            del kwargs[key]

    return await target.edit(**kwargs, reason=REASON)


async def ban_method(entry: AuditLogEntry):
    if not isinstance(entry.target, (Object, Snowflake)):  # type: ignore
        return

    return await entry.guild.ban(entry.target, reason=REASON)


async def unban_method(entry: AuditLogEntry):
    if not isinstance(entry.target, (Object, Snowflake)):  # type: ignore
        return

    return await entry.guild.unban(entry.target, reason=REASON)


REVERT_METHODS: Dict[
    str, Dict[AuditLogAction, Callable[[AuditLogEntry], Coroutine[Any, Any, None]]]
] = {
    "create": {
        AuditLogAction.channel_create: delete_method,
        AuditLogAction.emoji_create: delete_method,
        AuditLogAction.sticker_create: delete_method,
        AuditLogAction.webhook_create: delete_method,
        AuditLogAction.role_create: delete_method,
        AuditLogAction.ban: unban_method,
    },
    "update": {
        AuditLogAction.guild_update: edit_method,
        AuditLogAction.channel_update: edit_method,
        AuditLogAction.emoji_update: edit_method,
        AuditLogAction.sticker_update: edit_method,
        AuditLogAction.webhook_update: edit_method,
        AuditLogAction.role_update: edit_method,
    },
    "delete": {
        AuditLogAction.unban: ban_method,
    },
}
