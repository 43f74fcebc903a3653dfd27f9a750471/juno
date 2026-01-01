import math
import random
from datetime import datetime, timedelta
from operator import attrgetter
from typing import (
    Any,
    AsyncIterable,
    AsyncIterator,
    Callable,
    Coroutine,
    Iterable,
    Iterator,
    List,
    Optional,
    Sequence,
    TypeVar,
    Union,
    overload,
)

T = TypeVar("T")
Coro = Coroutine[Any, Any, T]
_Iter = Union[Iterable[T], AsyncIterable[T]]


def human_join(seq: Sequence[str], delim: str = ", ", final: str = "or") -> str:
    size = len(seq)
    if size == 0:
        return ""

    if size == 1:
        return seq[0]

    if size == 2:
        return f"{seq[0]} {final} {seq[1]}"

    return delim.join(seq[:-1]) + f" {final} {seq[-1]}"


def human_size(value: int | float) -> str:
    size = ["B", "KB", "MB", "GB", "TB", "PB", "EB", "ZB", "YB"]
    i = 0
    while value >= 1024:
        value /= 1024
        i += 1

    return f"{value:.2f}{size[i]}"

def human_number(value: int | float) -> str:
    if isinstance(value, str):
        value = float(value)

    size = ["", "K", "M", "B", "T", "Q"]
    i = 0
    while value >= 1000:
        value /= 1000
        i += 1

    return f"{value:.2f}{size[i]}"

def scramble(value: str) -> str:
    scramed = ["".join(random.sample(word, len(word))) for word in value.split()]
    return " ".join(scramed)


class plural:
    value: str | int | list
    markdown: str

    def __init__(self, value: str | int | list, md: str = ""):
        self.value = value
        self.markdown = md

    def __format__(self, format_spec: str) -> str:
        v = self.value
        if isinstance(v, str):
            v = (
                int(v.split(" ", 1)[-1])
                if v.startswith(("CREATE", "DELETE"))
                else int(v)
            )

        elif isinstance(v, list):
            v = len(v)

        singular, _, plural = format_spec.partition("|")
        plural = plural or f"{singular}s"
        return (
            f"{self.markdown}{v:,}{self.markdown} {plural}"
            if abs(v) != 1
            else f"{self.markdown}{v:,}{self.markdown} {singular}"
        )


def vowel(value: str) -> str:
    if value[0].lower() in "aeiou":
        return "an " + value

    return "a " + value


def format_dt(value: datetime, spec: str) -> str:
    return f"<t:{int(value.timestamp())}:{spec}>"


class ts:
    def __init__(self, value: datetime) -> None:
        self.value: datetime = value

    def __format__(self, __format_spec: str) -> str:
        spec, _, _ = __format_spec.partition("|")
        return format_dt(self.value, spec)


def short_timespan(
    num_seconds: float | timedelta,
    max_units=3,
    delim: str = "",
) -> str:
    if isinstance(num_seconds, timedelta):
        num_seconds = num_seconds.total_seconds()

    units = [
        ("y", 60 * 60 * 24 * 365),
        ("mo", 60 * 60 * 24 * 30),
        ("w", 60 * 60 * 24 * 7),
        ("d", 60 * 60 * 24),
        ("h", 60 * 60),
        ("m", 60),
        ("s", 1),
        ("ms", 0.001),
    ]

    parts = []
    for unit, div in units:
        if num_seconds >= div:
            val = int(num_seconds // div)
            num_seconds %= div
            parts.append(f"{val}{unit}")
            if len(parts) == max_units:
                break

    return delim.join(parts)


def duration(value: float, ms: bool = True) -> str:
    h = int((value / (1000 * 60 * 60)) % 24) if ms else int((value / (60 * 60)) % 24)
    m = int((value / (1000 * 60)) % 60) if ms else int((value / 60) % 60)
    s = int((value / 1000) % 60) if ms else int(value % 60)

    result = ""
    if h:
        result += f"{h}:"

    result += f"{m}:" if m else "00:"
    result += f"{str(s).zfill(2)}" if s else "00"

    return result


def shorten(value: str, length: int = 24, remove_chars: bool = True) -> str:
    if remove_chars:
        BROKEN_HYPERLINK = ["[", "]", "(", ")"]
        for char in BROKEN_HYPERLINK:
            value = value.replace(char, "")

    value = value.replace("\n", " ")

    if len(value) <= length:
        return value

    return value[: length - 2] + ".."


def _format_not_finite(value: float) -> str:
    """Utility function to handle infinite and nan cases."""

    if math.isnan(value):
        return "NaN"
    if math.isinf(value) and value < 0:
        return "-Inf"
    if math.isinf(value) and value > 0:
        return "+Inf"

    return ""


def ordinal(value: int | str, fmt: int = 0) -> str:
    try:
        if not math.isfinite(float(value)):
            return _format_not_finite(float(value))
        value = int(value)
    except (TypeError, ValueError):
        return str(value)

    if fmt != 0:
        fmt_value = f"{str(value).zfill(fmt)}"
    else:
        fmt_value = format(value, ",")

    if value > 100_000:
        return fmt_value

    t = (
        "th",
        "st",
        "nd",
        "rd",
        "th",
        "th",
        "th",
        "th",
        "th",
        "th",
    )
    if value % 100 in (11, 12, 13):
        return f"{fmt_value}{t[0]}"

    return f"{fmt_value}{t[value % 10]}"


def hyperlink(text: str, url: str) -> str:
    return f"[{text}]({url})"


def hidden(value: str) -> str:
    return (
        "||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||||​||"
        f" _ _ _ _ _ _ {value}"
    )


async def _achunk(iterator: AsyncIterable[T], max_size: int) -> AsyncIterator[List[T]]:
    ret = []
    n = 0
    async for item in iterator:
        ret.append(item)
        n += 1
        if n == max_size:
            yield ret
            ret = []
            n = 0
    if ret:
        yield ret


def _chunk(iterator: Iterable[T], max_size: int) -> Iterator[List[T]]:
    ret = []
    n = 0
    for item in iterator:
        ret.append(item)
        n += 1
        if n == max_size:
            yield ret
            ret = []
            n = 0
    if ret:
        yield ret


@overload
def as_chunks(iterator: AsyncIterable[T], max_size: int) -> AsyncIterator[List[T]]: ...


@overload
def as_chunks(iterator: Iterable[T], max_size: int) -> Iterator[List[T]]: ...


def as_chunks(iterator: _Iter[T], max_size: int) -> _Iter[List[T]]:
    if max_size <= 0:
        raise ValueError("Chunk sizes must be greater than 0.")

    if isinstance(iterator, AsyncIterable):
        return _achunk(iterator, max_size)

    return _chunk(iterator, max_size)


def _find(predicate: Callable[[T], Any], iterable: Iterable[T], /) -> Optional[T]:
    return next((element for element in iterable if predicate(element)), None)


async def _afind(
    predicate: Callable[[T], Any], iterable: AsyncIterable[T], /
) -> Optional[T]:
    async for element in iterable:
        if predicate(element):
            return element

    return None


@overload
def find(
    predicate: Callable[[T], Any], iterable: AsyncIterable[T], /
) -> Coro[Optional[T]]: ...


@overload
def find(predicate: Callable[[T], Any], iterable: Iterable[T], /) -> Optional[T]: ...


def find(
    predicate: Callable[[T], Any], iterable: _Iter[T], /
) -> Union[Optional[T], Coro[Optional[T]]]:
    r"""A helper to return the first element found in the sequence
    that meets the predicate. For example: ::

        member = discord.utils.find(lambda m: m.name == 'Mighty', channel.guild.members)

    would find the first :class:`~discord.Member` whose name is 'Mighty' and return it.
    If an entry is not found, then ``None`` is returned.

    This is different from :func:`py:filter` due to the fact it stops the moment it finds
    a valid entry.

    .. versionchanged:: 2.0

        Both parameters are now positional-only.

    .. versionchanged:: 2.0

        The ``iterable`` parameter supports :term:`asynchronous iterable`\s.

    Parameters
    -----------
    predicate
        A function that returns a boolean-like result.
    iterable: Union[:class:`collections.abc.Iterable`, :class:`collections.abc.AsyncIterable`]
        The iterable to search through. Using a :class:`collections.abc.AsyncIterable`,
        makes this function return a :term:`coroutine`.
    """

    return (
        _afind(predicate, iterable)  # type: ignore
        if hasattr(
            iterable, "__aiter__"
        )  # isinstance(iterable, collections.abc.AsyncIterable) is too slow
        else _find(predicate, iterable)  # type: ignore
    )


def _get(iterable: Iterable[T], /, **attrs: Any) -> Optional[T]:
    # global -> local
    _all = all
    attrget = attrgetter

    # Special case the single element call
    if len(attrs) == 1:
        k, v = attrs.popitem()
        pred = attrget(k.replace("__", "."))
        return next((elem for elem in iterable if pred(elem) == v), None)

    converted = [
        (attrget(attr.replace("__", ".")), value) for attr, value in attrs.items()
    ]
    for elem in iterable:
        if _all(pred(elem) == value for pred, value in converted):
            return elem
    return None


async def _aget(iterable: AsyncIterable[T], /, **attrs: Any) -> Optional[T]:
    # global -> local
    _all = all
    attrget = attrgetter

    # Special case the single element call
    if len(attrs) == 1:
        k, v = attrs.popitem()
        pred = attrget(k.replace("__", "."))
        async for elem in iterable:
            if pred(elem) == v:
                return elem
        return None

    converted = [
        (attrget(attr.replace("__", ".")), value) for attr, value in attrs.items()
    ]

    async for elem in iterable:
        if _all(pred(elem) == value for pred, value in converted):
            return elem
    return None


@overload
def get(iterable: AsyncIterable[T], /, **attrs: Any) -> Coro[Optional[T]]: ...


@overload
def get(iterable: Iterable[T], /, **attrs: Any) -> Optional[T]: ...


def get(iterable: _Iter[T], /, **attrs: Any) -> Union[Optional[T], Coro[Optional[T]]]:
    r"""A helper that returns the first element in the iterable that meets
    all the traits passed in ``attrs``. This is an alternative for
    :func:`~discord.utils.find`.

    When multiple attributes are specified, they are checked using
    logical AND, not logical OR. Meaning they have to meet every
    attribute passed in and not one of them.

    To have a nested attribute search (i.e. search by ``x.y``) then
    pass in ``x__y`` as the keyword argument.

    If nothing is found that matches the attributes passed, then
    ``None`` is returned.

    .. versionchanged:: 2.0

        The ``iterable`` parameter is now positional-only.

    .. versionchanged:: 2.0

        The ``iterable`` parameter supports :term:`asynchronous iterable`\s.

    Examples
    ---------

    Basic usage:

    .. code-block:: python3

        member = discord.utils.get(message.guild.members, name='Foo')

    Multiple attribute matching:

    .. code-block:: python3

        channel = discord.utils.get(guild.voice_channels, name='Foo', bitrate=64000)

    Nested attribute matching:

    .. code-block:: python3

        channel = discord.utils.get(client.get_all_channels(), guild__name='Cool', name='general')

    Async iterables:

    .. code-block:: python3

        msg = await discord.utils.get(channel.history(), author__name='Dave')

    Parameters
    -----------
    iterable: Union[:class:`collections.abc.Iterable`, :class:`collections.abc.AsyncIterable`]
        The iterable to search through. Using a :class:`collections.abc.AsyncIterable`,
        makes this function return a :term:`coroutine`.
    \*\*attrs
        Keyword arguments that denote attributes to search with.
    """

    return (
        _aget(iterable, **attrs)  # type: ignore
        if hasattr(
            iterable, "__aiter__"
        )  # isinstance(iterable, collections.abc.AsyncIterable) is too slow
        else _get(iterable, **attrs)  # type: ignore
    )
