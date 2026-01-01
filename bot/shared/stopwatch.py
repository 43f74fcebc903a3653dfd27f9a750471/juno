from __future__ import annotations

from logging import Logger, getLogger
from operator import eq
from time import perf_counter
from traceback import format_exc
from types import TracebackType
from typing import Optional

__all__ = ("Stopwatch",)


class Stopwatch:
    def __init__(self) -> None:
        self.__start_time: float = 0.0
        self.__stop_time: float = 0.0
        self.__logger = getLogger("bot.stopwatch")

    def start(self) -> None:
        self.start_time = perf_counter()

    def stop(self) -> None:
        self.stop_time = perf_counter()

    def reset(self) -> float:
        elapsed = self.elapsed
        self.start_time = 0.0
        self.stop_time = 0.0
        return elapsed

    @property
    def start_time(self) -> float:
        return self.__start_time

    @start_time.setter
    def start_time(self, value: float) -> None:
        self.__start_time = value

    @property
    def stop_time(self) -> float:
        return self.__stop_time

    @stop_time.setter
    def stop_time(self, value: float) -> None:
        self.__stop_time = value

    @property
    def elapsed(self) -> float:
        if eq(self.start_time, 0.0):
            return 0.0

        return (
            self.stop_time - self.start_time
            if self.stop_time
            else perf_counter() - self.start_time
        )

    @property
    def logger(self) -> Logger:
        return self.__logger

    def __await__(self) -> Stopwatch:
        self.start()
        return self

    def __aenter__(self) -> Stopwatch:
        self.start()
        return self

    async def __aexit__(
        self,
        exc_type: Optional[type[BaseException]],
        exc_val: Optional[BaseException],
        exc_tb: Optional[TracebackType],
    ) -> None:
        self.stop()

        if all((exc_type, exc_val, exc_tb)):
            return self.logger.error(format_exc())

    def __enter__(self) -> Stopwatch:
        self.start()
        return self

    def __exit__(
        self,
        exc_type: Optional[type[BaseException]],
        exc_val: Optional[BaseException],
        exc_tb: Optional[TracebackType],
    ) -> None:
        self.stop()

        if all((exc_type, exc_val, exc_tb)):
            return self.logger.error(format_exc())
