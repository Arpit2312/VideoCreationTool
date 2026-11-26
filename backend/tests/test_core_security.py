"""Zero-cost env filtering, secret redaction and the path guard."""
from pathlib import Path
from types import SimpleNamespace

import pytest

from spacestudio.security import PathRefused, child_env, guard, redact, redact_obj

BASE = {"PATH": "/usr/bin", "HOME": "/home/u", "LANG": "C.UTF-8", "TERM": "xterm", "USER": "u",
        "ANTHROPIC_API_KEY": "sk-ant-secretvalue123", "CLAUDE_CODE_USE_BEDROCK": "0",
        "CLAUDE_CONFIG_DIR": "/home/u/.claude", "HTTPS_PROXY": "http://proxy:3128",
        "ELEVENLABS_API_KEY": "el-key-123456", "OPENAI_API_KEY": "sk-openai-999999999",
        "PEXELS_API_KEY": "pexels-free-1234", "AWS_SECRET_ACCESS_KEY": "awssecret12345", "RANDOM_VAR": "x"}
SETTINGS = SimpleNamespace(extra_env={"MY_FLAG": "1"}, allowed_free_keys=["PEXELS_API_KEY"])
PAID = {"ELEVENLABS_API_KEY", "OPENAI_API_KEY", "PEXELS_API_KEY"}


def test_minimal_allowlist_and_claude_vars():
    env = child_env(SETTINGS, {"ENGINE_MODE": "a"}, zero_cost=True, paid_env_names=PAID, base_env=BASE)
    for k in ("PATH", "HOME", "LANG", "TERM", "USER", "ANTHROPIC_API_KEY", "CLAUDE_CODE_USE_BEDROCK",
              "CLAUDE_CONFIG_DIR", "HTTPS_PROXY", "MY_FLAG", "ENGINE_MODE"):
        assert k in env, k
    assert "RANDOM_VAR" not in env and "AWS_SECRET_ACCESS_KEY" not in env


def test_zero_cost_drops_paid_keys_but_keeps_allowed_free_keys():
    env = child_env(SETTINGS, {"OPENAI_API_KEY": "sneaky"}, zero_cost=True, paid_env_names=PAID, base_env=BASE)
    assert "ELEVENLABS_API_KEY" not in env and "OPENAI_API_KEY" not in env
    assert env["PEXELS_API_KEY"] == "pexels-free-1234"


def test_paid_keys_only_for_approved_providers():
    env = child_env(SETTINGS, {}, zero_cost=False, approved_paid_providers=["elevenlabs"],
                    paid_env_names=PAID, base_env=BASE)
    assert env["ELEVENLABS_API_KEY"] == "el-key-123456"
    assert "OPENAI_API_KEY" not in env
    env2 = child_env(SETTINGS, {}, zero_cost=False, approved_paid_providers=["OPENAI_API_KEY"],
                     paid_env_names=PAID, base_env=BASE)
    assert "OPENAI_API_KEY" in env2 and "ELEVENLABS_API_KEY" not in env2


def test_redact_env_values_and_patterns():
    env = {"MY_SERVICE_TOKEN": "tok_abcdef123456", "HOME": "/home/u"}
    text = "using tok_abcdef123456 and sk-abcdefghijklmnop api_key=hunter2hunter token: zzzz9999 Bearer abc.def.ghi123"
    out = redact(text, env)
    for secret in ("tok_abcdef123456", "sk-abcdefghijklmnop", "hunter2hunter", "zzzz9999", "abc.def.ghi123"):
        assert secret not in out
    assert "api_key=***" in out
    assert redact("nothing secret here") == "nothing secret here"


def test_redact_obj_masks_secret_named_keys():
    out = redact_obj({"OPENAI_API_KEY": "plainvalue", "nested": ["password=abcd1234"], "n": 3})
    assert out["OPENAI_API_KEY"] == "***" and "abcd1234" not in out["nested"][0] and out["n"] == 3


@pytest.mark.parametrize("rel", ["../x", "../../etc/passwd", "/etc/passwd", "a/../../b", "scripts/../../x"])
def test_guard_refuses_escape(tmp_path, rel):
    with pytest.raises(PathRefused):
        guard(tmp_path, rel)


def test_guard_allows_inside(tmp_path):
    (tmp_path / "scripts").mkdir()
    assert guard(tmp_path, "scripts/script.json") == (tmp_path / "scripts" / "script.json").resolve()
    assert guard(tmp_path, "") == tmp_path.resolve()


def test_guard_refuses_symlink_escape(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "link").symlink_to(tmp_path)
    with pytest.raises(PathRefused):
        guard(ws, "link/secret")
