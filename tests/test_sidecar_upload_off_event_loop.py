"""mtDNA and zarohla must store an upload without stalling their event loop.

Each runs one gunicorn worker, and the arbiter SIGKILLs a worker whose event loop
misses its heartbeat for 30 s. Both wrote every chunk of an upload with a plain
write() on the loop. Storing a 41 GB NA12878 BAM while the other sidecars stored the
same file (2026-10-05), one write stalled long enough that mtDNA's worker hit
WORKER TIMEOUT twice, 8.4 GB in, and the job failed. store_upload() writes in a
thread; this drives each module's real store_upload with a write that blocks, and
checks that the loop kept running meanwhile.
"""

from __future__ import annotations

import ast
import asyncio
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SIDECARS = {
    "mtdna": ROOT / "docker" / "mtdna-server-2" / "app.py",
    "zarohla": ROOT / "docker" / "zarohla" / "app.py",
}


def _store_upload(source: Path, blocking_open):
    """The module's own store_upload, with `open` swapped for a slow writer."""
    tree = ast.parse(source.read_text(encoding="utf-8"))
    wanted = [
        node
        for node in tree.body
        if (isinstance(node, ast.AsyncFunctionDef) and node.name == "store_upload")
        or (
            isinstance(node, ast.Assign)
            and any(
                getattr(t, "id", None) == "UPLOAD_CHUNK_BYTES" for t in node.targets
            )
        )
    ]
    assert len(wanted) == 2, f"store_upload or UPLOAD_CHUNK_BYTES missing in {source}"
    ns = {"asyncio": asyncio, "open": blocking_open, "UploadFile": object}
    exec(compile(ast.Module(body=wanted, type_ignores=[]), str(source), "exec"), ns)
    return ns["store_upload"]


class _Upload:
    def __init__(self, chunks):
        self.chunks = list(chunks)

    async def read(self, size):
        return self.chunks.pop(0) if self.chunks else b""


class _SlowFile:
    """A file whose every write blocks, the way a throttled disk does."""

    def __init__(self, sink):
        self.sink = sink

    def write(self, data):
        time.sleep(0.2)
        self.sink.append(data)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


@pytest.mark.parametrize("sidecar", sorted(SIDECARS))
def test_the_event_loop_keeps_running_while_a_write_blocks(sidecar):
    written = []
    store_upload = _store_upload(SIDECARS[sidecar], lambda *a, **k: _SlowFile(written))

    async def scenario():
        ticks = 0

        async def heartbeat():
            nonlocal ticks
            while True:
                ticks += 1
                await asyncio.sleep(0.01)

        beat = asyncio.create_task(heartbeat())
        await store_upload(_Upload([b"a", b"b", b"c"]), "ignored")
        beat.cancel()
        return ticks

    ticks = asyncio.run(scenario())

    assert written == [b"a", b"b", b"c"], "every chunk is still written, in order"
    # 0.6 s of blocked writes: about 60 ticks if the loop ran, 1-3 if it was held.
    assert ticks > 20, f"the event loop stalled during the writes ({ticks} ticks)"


DOCKERFILES = {
    "mtdna": ROOT / "docker" / "mtdna-server-2" / "Dockerfile",
    "zarohla": ROOT / "docker" / "zarohla" / "Dockerfile",
}


@pytest.mark.parametrize("sidecar", sorted(DOCKERFILES))
def test_the_worker_heartbeat_is_not_on_the_upload_disk(sidecar):
    """Moving the writes off the loop was not enough: the worker was still killed,
    because gunicorn's heartbeat file sat in /tmp on the disk the upload was being
    written to. The CMD must put it on tmpfs."""
    text = DOCKERFILES[sidecar].read_text(encoding="utf-8")
    cmd = next(line for line in text.splitlines() if line.startswith("CMD"))
    block = text[text.index(cmd) :]
    argv = ast.literal_eval(
        block[len("CMD") : block.index("]") + 1].replace("\\\n", "")
    )
    assert argv[0] == "gunicorn"
    assert argv[argv.index("--worker-tmp-dir") + 1] == "/dev/shm"
