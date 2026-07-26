"""Unit tests for e2e harness helpers (no docker)."""

from tests.e2e.harness import apply_e2e_env, e2e_requested, vacuous_e2e_failure


def test_e2e_requested_false_by_default():
    assert e2e_requested({}) is False
    assert e2e_requested({"ZAROPGX_E2E": "0"}) is False
    assert e2e_requested({"ZAROPGX_E2E": "true"}) is False


def test_e2e_requested_via_env():
    assert e2e_requested({"ZAROPGX_E2E": "1"}) is True


def test_e2e_requested_via_cli_flag_without_env():
    # Git Bash → Win32: env may be missing; CLI flag must still enable.
    assert e2e_requested({}, cli_flag=True) is True


def test_apply_e2e_env_sets_vars_from_cli_flag():
    env: dict[str, str] = {}
    assert apply_e2e_env(env, cli_flag=True) is True
    assert env["ZAROPGX_E2E"] == "1"
    assert env["ZAROPGX_E2E_BASE_URL"] == "http://127.0.0.1:18765"


def test_apply_e2e_env_preserves_existing_base_url():
    env = {"ZAROPGX_E2E": "1", "ZAROPGX_E2E_BASE_URL": "http://127.0.0.1:9999"}
    assert apply_e2e_env(env) is True
    assert env["ZAROPGX_E2E_BASE_URL"] == "http://127.0.0.1:9999"


def test_apply_e2e_env_noop_when_not_requested():
    env: dict[str, str] = {}
    assert apply_e2e_env(env) is False
    assert env == {}


def test_vacuous_e2e_failure_detects_skip_with_green_exit():
    assert vacuous_e2e_failure(requested=True, passed=0, exitstatus=0) is True


def test_vacuous_e2e_failure_ok_when_passed():
    assert vacuous_e2e_failure(requested=True, passed=1, exitstatus=0) is False


def test_vacuous_e2e_failure_ok_when_not_requested():
    assert vacuous_e2e_failure(requested=False, passed=0, exitstatus=0) is False


def test_vacuous_e2e_failure_ok_when_already_failed():
    assert vacuous_e2e_failure(requested=True, passed=0, exitstatus=1) is False
