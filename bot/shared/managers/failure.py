from typing import Any, Coroutine, Tuple, Type


async def aiterate(iterable):
    for item in iterable:
        yield item


class FailureManager:
    max_failures: int
    exception_types: Tuple[Type[Exception], ...]
    successes: int
    failures: int

    def __init__(
        self,
        max_failures: int = 0,
        exception_types: Tuple[Type[Exception], ...] = (Exception,),
    ):
        self.max_failures = max_failures
        self.exception_types = exception_types
        self.successes = 0
        self.failures = 0

    def __repr__(self):
        return f"<FailureManager successes={self.successes} failures={self.failures}>"

    async def __aiter__(self):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        if self.max_failures is not None and self.failures >= self.max_failures:
            raise RuntimeError("Cancelled early due to too many failures")

        return True

    async def attempt(self, coro: Coroutine) -> Any:
        try:
            return await coro
        except self.exception_types:
            self.failures += 1
            if self.max_failures is not None and self.failures >= self.max_failures:
                raise

            return None
