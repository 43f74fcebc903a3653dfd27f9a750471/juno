
import asyncio
import itertools

# from os import environ
import re
from asyncio.subprocess import Process
from contextlib import suppress
from logging import getLogger
from re import Pattern
from typing import Final, Optional

from wavelink import Node, Pool

from bot.core import Juno

logger = getLogger("bot.lavalink")

_FAILED_TO_START: Final[Pattern] = re.compile(rb"Web server failed to start\. (.*)")


class ServerManager:
    bot: Juno
    process: Optional[Process]
    pipe_task: Optional[asyncio.Task]
    ready: asyncio.Event
    shutdown: bool = False

    def __init__(self, bot: Juno) -> None:
        self.bot = bot
        self.process = None
        self.pipe_task = None
        self.ready = asyncio.Event()

    async def connect(self) -> None:
        await self.start()
        uri, password = "0.0.0.0:1738", "x2x1"
        # uri, password = "23.160.168.180:2333", "3o8RHRo0or0aMyCLf0HBHTpfgjcQ1S8zpOgnMMhhF7FACSNJpMU"
        # uri, password = "0.0.0.0:2333", "3o8RHRo0or0aMyCLf0HBHTpfgjcQ1S8zpOgnMMhhF7FACSNJpH"

        nodes = [
            Node(
                uri=f"http://{uri}",
                password=password,
                resume_timeout=180,
            )
        ]
        
        await Pool.connect(nodes=nodes, client=self.bot)

    async def start(self) -> None:
        try:
            self.process = await asyncio.create_subprocess_exec(
                "java",
                "-jar",
                "Lavalink.jar",
                cwd="lavalink/",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
        except FileNotFoundError:
            await self._install_jre()
            return await self.start()

        logger.info(f"Started Lavalink server on PID {self.process.pid}")
        try:
            await asyncio.wait_for(self._wait_for_launcher(), timeout=30)
        except asyncio.TimeoutError:
            logger.warning(
                "Timeout occurred whilst waiting for Lavalink node to be ready"
            )
            await self._partial_shutdown()
        except Exception:
            await self._partial_shutdown()
            raise

    async def wait_until_ready(self, timeout: float = 30):
        await asyncio.wait_for(self.ready.wait(), timeout=timeout)

    async def _install_jre(self) -> None:
        logger.error(
            "Java Runtime is not installed, run the following command to install it:"
        )
        logger.error("sudo apt-get install openjdk-17-jdk openjdk-17-jre -y")
        exit(1)

    async def _wait_for_launcher(self) -> None:
        assert self.process is not None
        assert self.process.stdout is not None

        for i in itertools.cycle(range(50)):
            line = await self.process.stdout.readline()
            if b"Lavalink is ready to accept connections." in line:
                self.ready.set()
                logger.info("Lavalink node is ready to accept connections")
                self.pipe_task = asyncio.create_task(self._pipe_output())
                break
            if _FAILED_TO_START.search(line):
                raise RuntimeWarning(
                    f"Lavalink failed to start: {line.decode().strip()}"
                )

            if self.process.returncode is not None:
                raise RuntimeWarning("Managed Lavalink node server exited early")
            if i == 49:
                await asyncio.sleep(0.1)

    async def _pipe_output(self):
        assert self.process is not None
        assert self.process.stdout is not None

        with suppress(asyncio.CancelledError):
            async for __ in self.process.stdout:
                pass

    async def _partial_shutdown(self) -> None:
        self.ready.clear()
        if self.shutdown is True:
            return

        if self.pipe_task:
            self.pipe_task.cancel()
        if self.process is not None:
            self.process.terminate()
            await self.process.wait()

        self.process = None
        self.shutdown = True
