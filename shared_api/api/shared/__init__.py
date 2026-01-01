from .services import services, Media
from .logger import build_logger
from pydantic import BaseModel as _BaseModel, ConfigDict, Field, computed_field
from contextlib import asynccontextmanager
from bs4 import BeautifulSoup
from ujson import loads, dumps
from typing import no_type_check, Awaitable, Callable, TypeVar
from typing_extensions import ParamSpec
from collections import deque
from functools import wraps, partial
import asyncio

T = TypeVar("T")
P = ParamSpec("P")


class BaseModel(_BaseModel):
    model_config = ConfigDict(
        coerce_numbers_to_str=True,
        populate_by_name=True,
    )


@asynccontextmanager
async def retry(attempts: int, wait: int):
    for _ in range(attempts):
        try:
            yield
            break
        except Exception:
            await asyncio.sleep(wait)
    else:
        raise


def executor_function(sync_function: Callable[P, T]) -> Callable[P, Awaitable[T]]:
    @wraps(sync_function)
    async def sync_wrapper(*args: P.args, **kwargs: P.kwargs):
        """
        Asynchronous function that wraps a sync function with an executor.
        """

        loop = asyncio.get_event_loop()
        internal_function = partial(sync_function, *args, **kwargs)
        return await loop.run_in_executor(None, internal_function)

    return sync_wrapper


@no_type_check
@executor_function
def extract_json(data, key_ident: str, soap=True) -> bytes:
    def seq_checker(value):
        if len(value) == 1:
            value = [value]
        to_check = deque(value)

        def mapping_checker(value: dict):
            for v in value.values():
                if type(v) == list:
                    to_check.extend(v)
                    continue
                if type(v) == dict:
                    to_check.append(v)
                    continue
                if type(v) == str:
                    try:
                        data = loads(v)
                        to_check.append(data)
                    except Exception:
                        continue

        rounds = 0
        while to_check:
            item = to_check.pop()
            rounds += 1
            if type(item) == list:
                to_check.extend(item)
                continue
            if type(item) == dict:
                if key_ident in item:
                    return item
                item = mapping_checker(item)
                if type(item) == list:
                    to_check.extend(result)
                    continue
            if type(item) == str:
                try:
                    item = loads(item)
                    to_check.append(item)
                except Exception:
                    continue

        return None

    if soap:
        soup = BeautifulSoup(data, "lxml")
        for x in soup.find_all("script", type="application/json"):
            result = seq_checker(loads(x.decode_contents()))
            if result:
                return dumps(result)

    else:
        result = seq_checker(loads(data))
        if result:
            return dumps(result)

    return False


__all__ = (
    "services",
    "Media",
    "BaseModel",
    "Field",
    "computed_field",
    "build_logger",
    "retry",
    "executor_function",
    "extract_json",
)
