from json import dumps, loads
from typing import Any, List, Optional, Tuple, Union, cast

from asyncpg import Connection, Pool
from asyncpg import Record as DefaultRecord
from asyncpg import create_pool

from config import config


class Record(DefaultRecord):
    def __getattr__(self, name: Union[str, Any]) -> Any:
        attr: Any = self[name]
        return attr

    def __setitem__(self, name: Union[str, Any], value: Any) -> None:
        self.__dict__[name] = value

    def to_dict(self) -> dict[str, Any]:
        return dict(self)


class Database(Pool):
    async def execute(
        self,
        query: str,
        *args: Any,
        timeout: Optional[float] = None,
    ) -> str: ...

    async def fetch(
        self,
        query: str,
        *args: Any,
        timeout: Optional[float] = None,
    ) -> List[Record]: ...

    async def fetchrow(
        self,
        query: str,
        *args: Any,
        timeout: Optional[float] = None,
    ) -> Optional[Record]: ...

    async def fetchval(
        self,
        query: str,
        *args: Any,
        timeout: Optional[float] = None,
    ) -> Optional[str | int]: ...


async def init(connection: Connection) -> None:
    await connection.set_type_codec(
        "JSONB",
        schema="pg_catalog",
        encoder=dumps,
        decoder=loads,
    )
    await connection.set_type_codec(
        "numeric",
        schema="pg_catalog",
        encoder=str,
        decoder=float,
        format="text",
    )
    with open("bot/core/database/schema.sql") as buffer:
        schema = buffer.read()
        await connection.execute(schema)


async def connect() -> Tuple[Database, str, int]:
    pool = cast(
        Optional[Database],
        await create_pool(
            dsn=config.postgres.dsn,
            record_class=Record,
            init=init,
            min_size=20,
            max_size=20,
            statement_cache_size=0,
        ),
    )
    if not pool:
        raise RuntimeError("Failed to connect to the database.")

    async with pool.acquire() as connection:
        version = (await connection.fetchval("SELECT version()")).split("(")[0].strip()
        pid = connection.get_server_pid()

    return pool, version, pid
