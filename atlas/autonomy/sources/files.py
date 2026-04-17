"""Filesystem source — watch a directory for saves using watchdog.

Emits:
  - file_saved (path, ext)
  - file_created
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from atlas.autonomy.models import Signal

logger = logging.getLogger("atlas.autonomy.files")


class FilesSource:
    source = "files"

    def __init__(self, root: Path | None = None) -> None:
        self.root = (root or Path("~/Documents").expanduser()).resolve()
        self._queue: asyncio.Queue[Signal] = asyncio.Queue()
        self._observer = None

    async def start(self) -> None:
        if self._observer is not None:
            return
        try:
            from watchdog.events import FileSystemEventHandler  # type: ignore
            from watchdog.observers import Observer  # type: ignore
        except ImportError:
            logger.warning("watchdog not installed — files source disabled")
            return
        loop = asyncio.get_running_loop()
        q = self._queue

        class Handler(FileSystemEventHandler):
            def on_modified(self, event):
                if event.is_directory:
                    return
                loop.call_soon_threadsafe(
                    q.put_nowait,
                    Signal(source="files", kind="file_saved",
                           payload={"path": event.src_path, "ext": Path(event.src_path).suffix}),
                )

            def on_created(self, event):
                if event.is_directory:
                    return
                loop.call_soon_threadsafe(
                    q.put_nowait,
                    Signal(source="files", kind="file_created",
                           payload={"path": event.src_path, "ext": Path(event.src_path).suffix}),
                )

        self._observer = Observer()
        self._observer.schedule(Handler(), str(self.root), recursive=True)
        self._observer.start()

    async def poll(self) -> list[Signal]:
        if self._observer is None:
            await self.start()
        out: list[Signal] = []
        while not self._queue.empty():
            out.append(self._queue.get_nowait())
        return out

    def stop(self) -> None:
        if self._observer is not None:
            self._observer.stop()
            self._observer.join(timeout=2)
            self._observer = None
