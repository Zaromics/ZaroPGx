"""start-docker.sh opens to zarohla and mtdna every path they write in data/.

Both run as their own user (uid 999), so on a native Linux host they can write
only what start-docker.sh makes writable. It touched hlatyping_progress.log, which
nothing writes, while zarohla writes zarohla_progress.log. It left data/versions
755, where both publish their tool's version at startup, so the Software Platform
table of every report had no row for mtDNA-Server 2 or OptiType.
"""

from __future__ import annotations

import re
import stat
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SIDECARS = ("docker/zarohla/app.py", "docker/mtdna-server-2/app.py")


def _written_under_data():
    """The data/ paths each uid-999 sidecar writes by default."""
    for path in SIDECARS:
        source = (ROOT / path).read_text(encoding="utf-8")
        yield from re.findall(r'DATA_DIR / "([\w.]+)"', source)


def _permissions_block():
    script = (ROOT / "start-docker.sh").read_text(encoding="utf-8")
    start = script.index("mkdir -p data/temp/mtdna")
    return script[start : script.index("\ndone", start) + len("\ndone")]


def test_the_sidecars_can_write_what_they_write(tmp_path):
    written = set(_written_under_data())
    assert {"versions", "zarohla_progress.log", "mtdna_progress.log"} <= written
    subprocess.run(["bash", "-c", _permissions_block()], cwd=tmp_path, check=True)
    for name in sorted(written):
        mode = (tmp_path / "data" / name).stat().st_mode
        if stat.S_ISDIR(mode):
            assert mode & 0o1777 == 0o1777, name
        else:
            assert mode & 0o666 == 0o666, name
