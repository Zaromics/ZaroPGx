"""Structural and focused behavioral contracts for one-command installation."""

from __future__ import annotations

import re
import shlex
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
RELEASE = "0.4.0"
ZAROPGX_SERVICES = {
    "app",
    "genome-downloader",
    "pharmcat",
    "gatk-api",
    "pypgx",
    "zarohla",
    "mtdna",
    "nextflow",
}


def _text(name: str) -> str:
    return (ROOT / name).read_text(encoding="utf-8")


def _env_values(name: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in _text(name).splitlines():
        if not line or line.lstrip().startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip()
    return values


def _bash_function(script: str, name: str) -> str:
    """Extract a simple top-level Bash function for an isolated behavior test."""
    text = _text(script)
    match = re.search(rf"(?m)^{re.escape(name)}\(\) \{{\n", text)
    assert match, f"{script} must define {name}()"
    depth = 1
    position = match.end()
    while position < len(text) and depth:
        char = text[position]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
        position += 1
    assert depth == 0, f"{script}:{name} has unbalanced braces"
    return text[match.start() : position]


def test_release_checkout_and_image_profiles_are_pinned_together():
    bash = _text("bootstrap.sh")
    powershell = _text("bootstrap.ps1")
    assert f'RELEASE_VERSION="{RELEASE}"' in bash
    assert 'BRANCH="v${RELEASE_VERSION}"' in bash
    assert f'[string]$ReleaseVersion = "{RELEASE}"' in powershell
    assert '[string]$Branch = ""' in powershell
    assert '$Branch = "v$ReleaseVersion"' in powershell
    for profile in (".env.example", ".env.local", ".env.production"):
        assert _env_values(profile)["ZAROPGX_TAG"] == RELEASE, profile


def test_update_force_fetches_the_selected_release_tag(tmp_path: Path):
    bash = _text("bootstrap.sh")
    powershell = _text("bootstrap.ps1")
    fetch = "fetch --all --prune --force --tags"
    assert fetch in bash
    assert fetch in powershell

    origin = tmp_path / "origin.git"
    publisher = tmp_path / "publisher"
    clone = tmp_path / "clone"
    subprocess.run(
        ["git", "init", "--bare", str(origin)], check=True, capture_output=True
    )
    subprocess.run(["git", "init", str(publisher)], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(publisher), "config", "user.email", "bootstrap@test.invalid"],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(publisher), "config", "user.name", "Bootstrap Test"],
        check=True,
    )
    tracked = publisher / "release.txt"
    tracked.write_text("old\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(publisher), "add", "release.txt"], check=True)
    subprocess.run(
        ["git", "-C", str(publisher), "commit", "-m", "old"],
        check=True,
        capture_output=True,
    )
    subprocess.run(["git", "-C", str(publisher), "tag", f"v{RELEASE}"], check=True)
    subprocess.run(
        ["git", "-C", str(publisher), "remote", "add", "origin", str(origin)],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(publisher), "push", "origin", "HEAD", "--tags"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "clone", str(origin), str(clone)], check=True, capture_output=True
    )
    old_tag = subprocess.check_output(
        ["git", "-C", str(clone), "rev-parse", f"v{RELEASE}"],
        text=True,
    ).strip()

    tracked.write_text("new\n", encoding="utf-8")
    subprocess.run(
        ["git", "-C", str(publisher), "commit", "-am", "new"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(publisher), "tag", "-f", f"v{RELEASE}"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        [
            "git",
            "-C",
            str(publisher),
            "push",
            "--force",
            "origin",
            f"refs/tags/v{RELEASE}",
        ],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(clone), "fetch", "--all", "--prune", "--force", "--tags"],
        check=True,
        capture_output=True,
    )
    new_tag = subprocess.check_output(
        ["git", "-C", str(clone), "rev-parse", f"v{RELEASE}"],
        text=True,
    ).strip()
    assert new_tag != old_tag


def test_all_zaropgx_services_explicitly_request_the_published_platform():
    yaml = pytest.importorskip("yaml")
    compose = yaml.safe_load(_text("compose.yml"))
    services = compose["services"]
    found = {
        name
        for name, definition in services.items()
        if str(definition.get("image", "")).startswith("zaromicsresearch/zaropgx-")
    }
    assert found == ZAROPGX_SERVICES
    for name in found:
        assert services[name].get("platform") == "linux/amd64", name
        assert f"${{ZAROPGX_TAG:-{RELEASE}}}" in services[name]["image"], name


def _run_bash_migration(
    tmp_path: Path, value: str, release: str, newline: str = "\n"
) -> tuple[str, str]:
    """Run start-docker.sh's _migrate_release_tag on a .env holding `value`.

    Returns the ZAROPGX_TAG left in .env and what the function printed.
    """
    migration = _bash_function("start-docker.sh", "_migrate_release_tag")
    env_file = tmp_path / ".env"
    env_file.write_bytes(
        f"LOG_LEVEL=INFO{newline}ZAROPGX_TAG={value}{newline}KEEP=1{newline}".encode()
    )
    command = f"""
set -euo pipefail
{migration}
cd {shlex.quote(str(tmp_path))}
_migrate_release_tag {shlex.quote(release)}
"""
    result = subprocess.run(
        ["bash", "-c", command], check=True, text=True, capture_output=True
    )
    values = _env_values_from_path(env_file)
    assert values["LOG_LEVEL"] == "INFO" and values["KEEP"] == "1", "other keys kept"
    return values["ZAROPGX_TAG"], result.stdout


def test_bash_known_stale_release_migration_preserves_custom_tags(tmp_path: Path):
    def migrate(value: str) -> str:
        return _run_bash_migration(tmp_path, value, RELEASE)[0]

    assert migrate("0.2.8") == RELEASE
    assert migrate("") == ""
    assert migrate("latest") == "latest"
    assert migrate("custom-build") == "custom-build"


@pytest.mark.parametrize("old", ["0.2.8", "0.3.0", "0.3.1", "0.3.2", "0.3.9"])
def test_bash_migration_moves_an_older_release_up(tmp_path: Path, old: str):
    """An updated checkout must not keep running the old release's service images.

    0.3.9 against 0.3.10 pins the numeric, field-by-field comparison: compared as
    text, "0.3.9" sorts after "0.3.10" and would be left behind.
    """
    tag, printed = _run_bash_migration(tmp_path, old, "0.3.10")
    assert tag == "0.3.10"
    assert old in printed and "0.3.10" in printed


def test_bash_migration_reads_a_crlf_env(tmp_path: Path):
    tag, _ = _run_bash_migration(tmp_path, "0.3.2", "0.3.10", newline="\r\n")
    assert tag == "0.3.10"


@pytest.mark.parametrize(
    "kept",
    [
        "0.3.10",  # this release
        "0.4.0",  # newer
        "1.0.0",
        "",
        "latest",
        "local-friday",
        "custom-build",
        "0.4.0-rc1",
        "0.3.2-rc1",  # older, but not a plain release
        "v0.3.2",
        "0.3",
    ],
)
def test_bash_migration_leaves_operator_tags_alone(tmp_path: Path, kept: str):
    tag, printed = _run_bash_migration(tmp_path, kept, "0.3.10")
    assert tag == kept
    assert printed == ""


def test_bash_migration_needs_a_plain_release_to_move_to(tmp_path: Path):
    tag, _ = _run_bash_migration(tmp_path, "0.3.2", "0.4.0-rc1")
    assert tag == "0.3.2"


def test_powershell_migration_moves_older_plain_releases_only():
    powershell = _text("start-docker.ps1")
    start = powershell.index("function Update-KnownReleaseTag")
    body = powershell[start : powershell.index("\nfunction ", start + 1)]
    assert "$plainRelease = '^[0-9]+\\.[0-9]+\\.[0-9]+$'" in body
    assert (
        "if ($current -notmatch $plainRelease -or $ReleaseVersion -notmatch "
        "$plainRelease) { return }"
    ) in body
    assert "$older = [version]$current -lt [version]$ReleaseVersion" in body
    assert "if (-not $older) { return }" in body
    assert '$current -ne "0.2.8"' not in body
    # Write, then read back: the same verify-after-write as before.
    assert body.index("Set-EnvVarInFile") < body.index(
        '(Get-EnvVarFromFile -Key "ZAROPGX_TAG") -ne $ReleaseVersion'
    )


def _env_values_from_path(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line or line.lstrip().startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip()
    return values


def test_start_scripts_migrate_before_stopping_the_stack():
    bash = _text("start-docker.sh")
    assert f'RELEASE_VERSION="{RELEASE}"' in bash
    assert bash.index('_migrate_release_tag "$RELEASE_VERSION"') < bash.index(
        "docker compose down --remove-orphans"
    )

    powershell = _text("start-docker.ps1")
    assert f'$ReleaseVersion = "{RELEASE}"' in powershell
    assert powershell.index(
        "Update-KnownReleaseTag -ReleaseVersion $ReleaseVersion"
    ) < (powershell.index('Invoke-Docker "docker compose down --remove-orphans"'))


def test_bootstraps_require_compose_v2_24_or_newer():
    bash = _text("bootstrap.sh")
    powershell = _text("bootstrap.ps1")
    assert "require_compose_v2" in bash
    assert "2.24.0" in bash
    assert "command -v docker-compose" not in bash
    assert "Test-DockerComposeVersion" in powershell
    assert '[version]"2.24.0"' in powershell


def test_bash_compose_version_gate_rejects_old_v2(tmp_path: Path):
    gate = _bash_function("bootstrap.sh", "require_compose_v2")
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    docker = fake_bin / "docker"
    docker.write_text(
        '#!/bin/sh\nprintf "%s\\n" "$FAKE_COMPOSE_VERSION"\n',
        encoding="utf-8",
    )
    docker.chmod(0o755)

    def accepted(version: str) -> bool:
        command = f"""
set -uo pipefail
{gate}
PATH={shlex.quote(str(fake_bin))}:$PATH
FAKE_COMPOSE_VERSION={shlex.quote(version)}
export PATH FAKE_COMPOSE_VERSION
require_compose_v2
"""
        result = subprocess.run(["bash", "-c", command], text=True, capture_output=True)
        return result.returncode == 0

    assert not accepted("2.23.3")
    assert accepted("2.24.0")
    assert accepted("5.5.1")


def test_wsl_probe_uses_the_docker_exit_code_not_echo_exit_code():
    powershell = _text("start-docker.ps1")
    assert "docker info >/dev/null 2>&1'; echo `$?" not in powershell
    assert "docker info >/dev/null 2>&1; echo `$?" not in powershell
    assert "-or $exitCode -eq 0" not in powershell


def test_startup_rejects_a_failed_wsl_update():
    powershell = _text("start-docker.ps1")
    update_block = re.search(
        r"wsl --update(?P<body>.*?)WSL update complete",
        powershell,
        re.DOTALL,
    )
    assert update_block
    assert "$LASTEXITCODE -ne 0" in update_block.group("body")
    assert "exit 1" in update_block.group("body")


def test_powershell_checks_native_wsl_install_and_update_exit_codes():
    powershell = _text("bootstrap.ps1")
    assert (
        powershell.count('if ($LASTEXITCODE -ne 0) { throw "WSL installation failed')
        == 2
    )
    assert powershell.count('if ($LASTEXITCODE -ne 0) { throw "WSL update failed') == 2
    assert powershell.count("if (-not $script:NeedsReboot) {") >= 2


def test_powershell_bootstrap_propagates_startup_failure():
    powershell = _text("bootstrap.ps1")
    failure_branch = re.search(
        r"if \(\$startScriptExitCode -ne 0\) \{(?P<body>.*?)\n\s*\}",
        powershell,
        re.DOTALL,
    )
    assert failure_branch
    assert "exit $startScriptExitCode" in failure_branch.group("body")


def test_powershell_update_refusals_exit_before_startup():
    powershell = _text("bootstrap.ps1")
    dirty_branch = re.search(
        r"if \(\$status\) \{(?P<body>.*?)\n\s*\}",
        powershell,
        re.DOTALL,
    )
    assert dirty_branch
    assert "exit 1" in dirty_branch.group("body")
    assert (
        "Existing directory is not a Git repository; refusing to update." in powershell
    )


def test_piped_powershell_elevation_downloads_a_complete_script():
    powershell = _text("bootstrap.ps1")
    install_function = powershell[powershell.index("function Install-Dependencies") :]
    install_function = install_function[
        : install_function.index("\n# Check dependencies")
    ]
    assert "$MyInvocation.MyCommand.ScriptBlock.ToString()" not in powershell
    assert "Downloading complete bootstrap script for elevation" in install_function
    assert "Invoke-WebRequest -Uri $downloadUrl" in install_function


def test_elevated_powershell_child_invokes_dependency_installer():
    powershell = _text("bootstrap.ps1")
    top_level = powershell[powershell.index("# Check dependencies") :]
    child_call = re.search(
        r"if \(\$SkipDependencyCheck -and \$MissingDeps\.Count -gt 0\) \{"
        r"(?P<body>.*?)\n\}",
        top_level,
        re.DOTALL,
    )
    assert child_call
    assert "Install-Dependencies -MissingDeps $MissingDeps" in child_call.group("body")
    assert child_call.start() < top_level.index("if (-not $SkipDependencyCheck) {")


def test_powershell_does_not_continue_with_missing_dependencies():
    powershell = _text("bootstrap.ps1")
    assert 'Read-ConsoleLine "Continue anyway?' not in powershell
    assert (
        "Re-run the same bootstrap command after completing those steps." in powershell
    )


def test_architecture_checks_query_the_docker_server():
    bash = _text("start-docker.sh")
    powershell = _text("start-docker.ps1")
    assert "{{.Server.Os}}/{{.Server.Arch}}" in bash
    assert 'arch="$(uname -m)"' not in bash
    assert "{{.Server.Os}}/{{.Server.Arch}}" in powershell
    assert "RuntimeInformation]::OSArchitecture" not in powershell


def test_documented_alpine_entry_installs_bash_and_curl_first():
    readme = _text("README.md")
    installation = _text("docs/user/installation.md")
    for document in (readme, installation):
        assert "apk add --no-cache bash curl" in document
