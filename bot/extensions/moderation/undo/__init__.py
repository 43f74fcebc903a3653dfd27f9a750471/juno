from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Coroutine, Dict, List, Optional

from discord import AuditLogEntry, Forbidden, Guild, Member, Message, User
from discord.ext.commands import BucketType, Cog, command, cooldown, has_permissions
from discord.ext.tasks import loop
from discord.utils import MISSING

from bot.core import Context, Juno
from bot.extensions.moderation.undo.methods import REVERT_METHODS


class Undo(Cog):
    reverted_actions: Dict[int, deque[int]] = defaultdict(deque)

    def __init__(self, bot: Juno) -> None:
        self.bot = bot

    async def cog_load(self) -> None:
        self.reverted_actions_cleanup.start()
        return await super().cog_load()

    async def cog_unload(self) -> None:
        self.reverted_actions_cleanup.cancel()
        return await super().cog_unload()

    @loop(minutes=30)
    async def reverted_actions_cleanup(self) -> None:
        self.reverted_actions.clear()

    async def get_audit_log(
        self,
        guild: Guild,
        user: Optional[User | Member] = None,
        after: Optional[datetime] = None,
        before: Optional[datetime] = None,
        include_reverted: bool = False,
    ) -> List[AuditLogEntry]:
        _after = datetime.now(tz=timezone.utc) - timedelta(days=7)
        after = max(after, _after) if after is not None else _after
        reverted_audit_logs = self.reverted_actions[guild.id]

        return [
            audit_log
            async for audit_log in guild.audit_logs(
                limit=100,
                user=user or MISSING,
                after=after,
                before=before or MISSING,
            )
            if (
                any(audit_log.action in actions for actions in REVERT_METHODS.values())
                and (audit_log.id not in reverted_audit_logs or include_reverted)
            )
        ][:5]

    @command(aliases=("revert", "ctrlz"))
    @has_permissions(administrator=True)
    @cooldown(1, 5, BucketType.guild)
    async def undo(
        self, ctx: Context, user: Optional[Member | User] = None
    ) -> Optional[Message]:
        """Revert the last action performed by a user."""

        audit_logs = await self.get_audit_log(ctx.guild, user=user)
        if not audit_logs:
            return await ctx.warn("There aren't any recent actions to revert")

        revert_method: Optional[
            Callable[[AuditLogEntry], Coroutine[Any, Any, None]]
        ] = None
        for audit_log in audit_logs:
            for method in REVERT_METHODS.values():
                if audit_log.action in method:
                    revert_method = method[audit_log.action]
                    break

            if revert_method:
                break

        if not revert_method:
            return await ctx.warn("There aren't any recent actions to revert")

        try:
            await revert_method(audit_log)
        except Forbidden:
            return await ctx.warn("I don't have permission to revert this action")
        except Exception as exc:
            return await ctx.warn(
                f"An error occurred while reverting the action: {exc}"
            )

        self.reverted_actions[ctx.guild.id].append(audit_log.id)
        return await ctx.add_check()
