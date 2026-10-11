"""nextflow.config asks the host for nothing a curl wrapper does not need.

Every process in main.nf runs in the nextflow container and calls a service over HTTP.
Nextflow's local executor refuses a task that requests more cpus or memory than the host
has ("Process requirement exceeds available memory"), so the old per-process requests
(30 GB for HLA typing, 4 CPUs for PharmCAT) failed jobs on small hosts while the work
itself ran in services with their own limits.
"""

import re
from pathlib import Path

PIPELINE = Path(__file__).resolve().parent.parent / "pipelines" / "pgx"
RUNNER = Path(__file__).resolve().parent.parent / "docker" / "nextflow" / "runner.py"


def _config() -> str:
    text = (PIPELINE / "nextflow.config").read_text(encoding="utf-8")
    return "\n".join(line.split("//", 1)[0] for line in text.splitlines())


def test_no_process_requests_cpus_or_memory():
    config = _config()
    assert not re.search(r"\bcpus\s*=", config)
    assert not re.search(r"\bmemory\s*=", config)


def test_every_selector_names_a_process_that_exists():
    main_nf = (PIPELINE / "main.nf").read_text(encoding="utf-8")
    processes = set(re.findall(r"^process\s+(\w+)", main_nf, re.M))
    for name in re.findall(r"withName:\s*['\"]?(\w+)", _config()):
        assert name in processes, f"nextflow.config selects missing process {name}"


def test_the_runners_profile_is_the_only_one():
    profile = re.search(r"'-profile',\s*'(\w+)'", RUNNER.read_text(encoding="utf-8"))
    assert profile and profile.group(1) == "standard"
    config = _config()
    body = config[config.index("profiles {") :]
    assert re.findall(r"^  (\w+)\s*\{", body, re.M)[:1] == ["standard"]
    assert "docker.enabled" not in config
    assert "conda.enabled" not in config
