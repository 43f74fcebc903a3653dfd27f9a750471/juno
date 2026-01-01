import logging
import platform
from contextlib import contextmanager
from os import system

import discord


class RemoveNoise(logging.Filter):
    def __init__(self) -> None:
        super().__init__(name="discord.state")

    def filter(self, record: logging.LogRecord) -> bool | logging.LogRecord:
        return record.levelname == "WARNING" and "rate" in record.msg


class ColorFormatter(discord.utils._ColourFormatter):
    LEVEL_COLOURS = [
        (logging.DEBUG, "\x1b[40;1m"),
        (logging.INFO, "\x1b[34;1m"),
        (logging.WARNING, "\x1b[33;1m"),
        (logging.ERROR, "\x1b[31m"),
        (logging.CRITICAL, "\x1b[41m"),
    ]

    FORMATS = {
        level: logging.Formatter(
            f"\x1b[30;1m%(asctime)s\x1b[0m {colour}%(levelname)-8s\x1b[0m \x1b[38;2;187;170;238m%(name)-15s\x1b[0m %(message)s",
            "%H:%M:%S",
        )
        for level, colour in LEVEL_COLOURS
    }

    def formatTime(self, record: logging.LogRecord, datefmt: str | None = None) -> str:
        if not hasattr(self, "_last_ts"):
            self._last_ts = record.created

        if record.created == self._last_ts:
            return " " * 8

        self._last_ts = record.created
        return super().formatTime(record, datefmt)

    def format(self, record: logging.LogRecord) -> str:
        return super().format(record)


@contextmanager
def setup_logging():
    system("cls" if platform == "win32" else "clear")

    log = logging.getLogger()
    try:  # __enter__
        discord.utils.setup_logging(formatter=ColorFormatter())
        logging.getLogger("discord.state").addFilter(RemoveNoise())
        log.setLevel(logging.INFO)
        for logger in ("discord", "discord.http", "httpx", "graphql_request"):
            logging.getLogger(logger).setLevel(logging.WARNING)

        yield
    finally:
        handlers = log.handlers[:]
        for handler in handlers:
            handler.close()
            log.removeHandler(handler)
