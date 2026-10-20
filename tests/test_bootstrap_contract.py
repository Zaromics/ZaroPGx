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


def _powershell_function(script: str, name: str) -> str:
    """Extract a top-level PowerShell function (its closing brace at column 0)."""
    start = script.index(f"\nfunction {name} {{\n") + 1
    return script[start : script.index("\n}\n", start) + 3]


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


def _git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], check=True, capture_output=True, text=True
    ).stdout.strip()


def _installed_previous_release(tmp_path: Path) -> tuple[Path, str]:
    """An install cloned at an older tag, with this release tagged on its origin.

    The published tree carries a stub start-docker.sh, so running bootstrap.sh
    --update against the install goes through the real fetch and checkout and
    ends in the stub instead of Docker. Returns the install and the new tag's commit.
    """
    origin = tmp_path / "origin.git"
    publisher = tmp_path / "publisher"
    install = tmp_path / "ZaroPGx"
    _git("init", "--bare", str(origin))
    _git("init", str(publisher))
    _git("-C", str(publisher), "config", "user.email", "bootstrap@test.invalid")
    _git("-C", str(publisher), "config", "user.name", "Bootstrap Test")
    # 0.3.2's .gitignore: *.log, which does not match a rotated *.log.1.
    (publisher / ".gitignore").write_text("*.log\n", encoding="utf-8")
    (publisher / "data").mkdir()
    (publisher / "data" / "about.md").write_text("old\n", encoding="utf-8")
    (publisher / "start-docker.sh").write_text(
        'echo "start-docker ran $*"\n', encoding="utf-8"
    )
    _git("-C", str(publisher), "add", ".")
    _git("-C", str(publisher), "commit", "-m", "previous release")
    _git("-C", str(publisher), "tag", "v0.3.2")
    (publisher / "data" / "about.md").write_text("new\n", encoding="utf-8")
    _git("-C", str(publisher), "commit", "-am", "this release")
    _git("-C", str(publisher), "tag", f"v{RELEASE}")
    release = _git("-C", str(publisher), "rev-parse", "HEAD")
    _git("-C", str(publisher), "remote", "add", "origin", str(origin))
    _git("-C", str(publisher), "push", "origin", "HEAD", "--tags")
    _git("clone", "--branch", "v0.3.2", str(origin), str(install))
    return install, release


def _bootstrap_update(install: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            "bash",
            str(ROOT / "bootstrap.sh"),
            "--skip-deps",
            "--update",
            "--dir",
            str(install),
        ],
        cwd=install.parent,
        text=True,
        capture_output=True,
        timeout=60,
    )


def test_update_runs_past_untracked_rotated_logs(tmp_path: Path):
    """The services rotate data/*_progress.log at 10 MB into .log.1 to .log.5.

    0.3.2's .gitignore does not match those, so they show as untracked. The
    update counted them as uncommitted changes and refused, on every install
    whose logs had rotated: the very command UPGRADING.md gives 0.3.2 users.
    """
    install, release = _installed_previous_release(tmp_path)
    rotated = install / "data" / "pharmcat_progress.log.1"
    rotated.write_text("rotated progress log\n", encoding="utf-8")
    assert _git("-C", str(install), "status", "--porcelain") == (
        "?? data/pharmcat_progress.log.1"
    )

    result = _bootstrap_update(install)

    assert result.returncode == 0, result.stderr
    assert "Repository updated successfully." in result.stdout
    assert "start-docker ran --auto-local" in result.stdout
    assert _git("-C", str(install), "rev-parse", "HEAD") == release
    assert rotated.read_text(encoding="utf-8") == "rotated progress log\n"


def test_update_still_refuses_local_changes_to_tracked_files(tmp_path: Path):
    install, _ = _installed_previous_release(tmp_path)
    previous = _git("-C", str(install), "rev-parse", "HEAD")
    (install / "data" / "about.md").write_text("edited here\n", encoding="utf-8")

    result = _bootstrap_update(install)

    assert result.returncode == 1
    assert "refusing to update" in result.stderr
    assert "start-docker ran" not in result.stdout
    assert _git("-C", str(install), "rev-parse", "HEAD") == previous
    assert (install / "data" / "about.md").read_text(encoding="utf-8") == (
        "edited here\n"
    )


def test_both_bootstraps_check_tracked_files_only_before_updating():
    bash = _text("bootstrap.sh")
    powershell = _text("bootstrap.ps1")
    assert '"$(git status --porcelain --untracked-files=no)"' in bash
    assert "status --porcelain)" not in bash
    # bootstrap.ps1 compares content with git diff, which never lists untracked files.
    update = powershell[
        powershell.index("Updating repository (fast-forward only)") : powershell.index(
            '"Skipping update'
        )
    ]
    assert "$trackedChanges = Get-TrackedChanges" in update
    assert "status --porcelain" not in powershell


def test_gitignore_covers_the_services_rotated_logs():
    for path in ("data/pharmcat_progress.log.1", "data/gatk_progress.log.5"):
        ignored = subprocess.run(
            ["git", "-C", str(ROOT), "check-ignore", "-q", "--no-index", path]
        )
        assert ignored.returncode == 0, f"{path} is not ignored"


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
    assert "Exit-Bootstrap $startScriptExitCode" in failure_branch.group("body")


def test_powershell_update_refusals_exit_before_startup():
    powershell = _text("bootstrap.ps1")
    dirty_branch = re.search(
        r"if \(\$trackedChanges\) \{(?P<body>.*?)\n\s*\}",
        powershell,
        re.DOTALL,
    )
    assert dirty_branch
    assert "Exit-Bootstrap 1" in dirty_branch.group("body")
    assert (
        "Existing directory is not a Git repository; refusing to update." in powershell
    )


def test_powershell_tracked_changes_compare_content_in_both_working_copy_and_index():
    """bootstrap.ps1 re-normalizes line endings with `git rm --cached -r .` and `git
    reset --hard`, which discard local changes to tracked files: the working copy's
    and the index's. `git diff HEAD` alone misses a change that was staged and then
    reverted in the working copy. git status would list a file whose only change is
    CRLF line endings (its size changed), refusing someone who edited nothing; git diff
    compares content after .gitattributes' eol rules. (PowerShell is not in CI; the
    branch was run under pwsh 7.4 in a container, see its PR text.)"""
    powershell = _text("bootstrap.ps1")
    function = _powershell_function(powershell, "Get-TrackedChanges")
    for diff in ("diff --name-status HEAD --", "diff --cached --name-status HEAD --"):
        # core.safecrlf=false: no "CRLF will be replaced by LF" warning per file.
        assert re.search(
            r"& git -c core\.safecrlf=false -C \$TargetDir "
            + re.escape(diff)
            + r"\n\s*if \(\$LASTEXITCODE -ne 0\) \{ throw ",
            function,
        ), diff
    assert "status --porcelain" not in powershell


def test_powershell_line_ending_resets_refuse_local_changes():
    """Every `git rm --cached -r .` + `git reset --hard` comes after a tracked-changes
    check whose failure, and whose finding changes, both exit first. Without -Update,
    a CRLF start-docker.sh used to trigger the reset with no check at all."""
    powershell = _text("bootstrap.ps1")
    resets = [
        m.start()
        for m in re.finditer(
            re.escape("git -C $TargetDir rm --cached -r ."), powershell
        )
    ]
    # -Update: before the checkout and after it. Without: a CRLF start-docker.sh.
    assert len(resets) == 3
    for reset in resets:
        before = powershell[:reset]
        guarded = before[before.rindex("$trackedChanges = Get-TrackedChanges") :]
        failed = re.search(r"\} catch \{(?P<body>.*?)\n\s*\}", guarded, re.DOTALL)
        assert failed, "the check's own failure must be handled"
        assert re.search(r"Exit-Bootstrap 1\s*$", failed["body"])
        dirty = re.search(
            r"\n\s*if \(\$trackedChanges\) \{(?P<body>.*?)\n\s*\}",
            guarded,
            re.DOTALL,
        )
        assert dirty
        assert re.search(r"Exit-Bootstrap 1\s*$", dirty["body"])

    update = powershell[
        powershell.index("Updating repository (fast-forward only)") : powershell.index(
            '"Skipping update'
        )
    ]
    # A file whose only change is CRLF line endings passes the check, but git
    # checkout still sees it as modified and refuses to replace it: re-normalize first.
    assert update.index("rm --cached -r .") < update.index("checkout $Branch")

    no_update = powershell[
        powershell.index('"Skipping update') : powershell.index("$startScript = ")
    ]
    # Still re-normalizes a CRLF checkout, the reset's purpose.
    assert '$content -match "`r`n"' in no_update
    assert "git -C $TargetDir reset --hard" in no_update


def test_powershell_refusals_say_stash_with_a_quoted_path():
    """A release install is a detached HEAD: a commit there would be left behind by
    -Update's checkout, so the refusals say stash, re-run, pop. The commands are
    copied as printed, so the path is quoted (it may contain spaces). `stash pop`
    without --index drops a change that was staged and then reverted in the working
    copy (checked under git 2.34), which the check now refuses for."""
    powershell = _text("bootstrap.ps1")
    refusals = re.findall(
        r"\n\s*if \(\$trackedChanges\) \{(.*?)\n\s*\}", powershell, re.DOTALL
    )
    assert len(refusals) == 2
    for refusal in refusals:
        said = "\n".join(line for line in refusal.splitlines() if "Write-Host" in line)
        assert "ommit" not in said
        assert 'git -C `"$TargetDir`" stash)' in said
        assert 'git -C `"$TargetDir`" stash pop --index' in said
        assert "git -C $TargetDir stash" not in said
    # The first is -Update's.
    assert "re-run with -Update" in refusals[0]
    assert "re-run with -Update" not in refusals[1]
    assert "Commit or stash" not in powershell


def test_powershell_exits_after_a_message_wait_for_enter():
    """Under `iwr | iex`, `exit` ends the caller's own PowerShell (checked under pwsh
    7.4), so a window opened to run the script closed before its last message could
    be read. Every exit after such a message goes through Exit-Bootstrap, which
    pauses only at a console: never under redirected input, where it would read the
    pipe, and never under -NonInteractive, where Read-Host fails."""
    powershell = _text("bootstrap.ps1")
    helper = _powershell_function(powershell, "Exit-Bootstrap")
    assert "[Environment]::UserInteractive" in helper
    assert "-not [Console]::IsInputRedirected" in helper
    assert re.search(
        r'try \{ \[void\]\(Read-Host "Press Enter to close"\) \} catch \{ \}', helper
    )
    assert re.search(r"exit \$Code\n\}\n$", helper)

    # The elevated installer runs in a window of its own that it holds open itself
    # (Start-Sleep, ReadKey), or whose result its caller reports (3010).
    elevated = powershell[
        powershell.index("# If SkipDependencyCheck is set and MissingDeps") : (
            powershell.index("# Install missing dependencies (normal flow")
        )
    ]
    rest = powershell.replace(helper, "").replace(elevated, "")
    bare = re.findall(r"(?m)^[^#\n]*?\bexit\s+(?:\d+|\$\w+)", rest)
    # Only the elevated install's own success, which prints nothing to read.
    assert [line.strip() for line in bare] == ["exit 0"]
    assert "if (-not $installed) { Exit-Bootstrap 1 }\n    exit 0\n" in powershell
    assert powershell.index("function Exit-Bootstrap") < powershell.index(
        "Exit-Bootstrap 1"
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


def test_bash_update_refusal_says_stash_not_commit():
    """A release install is a detached HEAD: a commit there is stranded by the
    update's checkout, so the refusal says to stash and restore, with the path
    quoted for copy-paste."""
    script = (ROOT / "bootstrap.sh").read_text(encoding="utf-8")
    assert "Commit or stash" not in script
    assert "stash), re-run with --update" in script
    assert "stash pop --index" in script
    assert 'git -C \\"${TARGET_DIR}\\"' in script
