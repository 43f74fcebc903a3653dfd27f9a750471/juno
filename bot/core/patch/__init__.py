from importlib import import_module, reload
from pathlib import Path

from .channel import * # noqa
from .gateway import * # noqa


def reload_patches():
    for patch in Path(__file__).parent.glob("*.py"):
        if patch.name == "__init__.py":
            continue

        module_name = f"bot.core.patch.{patch.stem}"
        reload(import_module(module_name))