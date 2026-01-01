from __future__ import annotations
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from bot.core import Juno

with open("bot/assets/ascii/banner.txt", "r") as f:
    BANNER = f.read()

def print_banner(bot: Juno):
    global BANNER
    replace = {
        "reset": "\033[0m",
        "purple": "\033[0m\033[95m",
        "bold_white": "\033[0m\033[1m",
        "juno": str(bot.user),
        "juno_version": str(bot.version),
        "guilds": format(len(bot.guilds), ","),
        "users": format(len(bot.users), ","),
        "cogs": str(len(bot.cogs)),
        "commands": str(len(set(bot.walk_commands()))),
        "db_version": bot.db_version,
        "db_pid": str(bot.db_pid),
        "backend_url": f"{bot.config.backend.public_url}/commands",
    }
    for key, value in replace.items():
        BANNER = BANNER.replace(f"${{{key}}}", value)

    print(BANNER)