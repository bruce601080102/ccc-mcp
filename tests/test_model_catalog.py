"""The model and thinking-level choices the console offers come from the installed CLIs."""

import json

import pytest
from starlette.testclient import TestClient

from ccc_mcp.adapters import model_catalog
from ccc_mcp.infra.errors import BridgeError
from ccc_mcp.interfaces.web import create_app

TOKEN = "test-session-token"

CODEX_CATALOG = json.dumps({"models": [
    {"slug": "gpt-a", "display_name": "GPT A", "description": "Fast", "visibility": "list",
     "default_reasoning_level": "low",
     "supported_reasoning_levels": [{"effort": "low", "description": "Quick"},
                                    {"effort": "high", "description": "Deep"}]},
    {"slug": "internal-reviewer", "display_name": "Internal", "visibility": "hide",
     "supported_reasoning_levels": [{"effort": "low"}]},
    {"slug": "gpt-b", "display_name": "GPT B", "visibility": "list",
     "supported_reasoning_levels": [{"effort": "medium"}, {"effort": "high"}]},
]})

CLAUDE_HELP = """
  --effort <level>                      Effort level for the current session
                                        (low, medium, high, xhigh, max)
  --model <model>                       Model for the current session. Provide
                                        an alias for the latest model (e.g.
                                        'fable', 'opus', or 'sonnet') or a
                                        model's full name (e.g.
                                        'claude-fable-5').
"""


@pytest.fixture(autouse=True)
def fresh_cache():
    model_catalog._cache.clear()
    yield
    model_catalog._cache.clear()


def test_codex_catalog_lists_only_models_meant_for_use_with_their_own_levels():
    models = model_catalog.parse_codex_models(CODEX_CATALOG)
    assert [m["id"] for m in models] == ["gpt-a", "gpt-b"]
    assert models[0]["label"] == "GPT A" and models[0]["efforts"] == ["low", "high"]
    assert models[0]["effort_notes"]["high"] == "Deep" and models[0]["default_effort"] == "low"
    assert models[1]["efforts"] == ["medium", "high"]


@pytest.mark.parametrize("text", ["not json", "{}", '{"models": [{"visibility": "hide", "slug": "x"}]}'])
def test_an_unusable_codex_answer_is_an_error_not_an_empty_list(text):
    with pytest.raises(BridgeError):
        model_catalog.parse_codex_models(text)


def test_claude_aliases_and_levels_are_read_from_its_help_text():
    assert model_catalog.parse_claude_help(CLAUDE_HELP) == (
        ["fable", "opus", "sonnet"], ["low", "medium", "high", "xhigh", "max"])
    assert model_catalog.parse_claude_help("nothing useful here") == ([], [])


def test_the_catalog_comes_from_the_cli_and_is_kept_for_a_while(monkeypatch):
    calls = []

    def run(argv):
        calls.append(argv[1:])
        return CODEX_CATALOG if argv[1] == "debug" else CLAUDE_HELP

    monkeypatch.setattr(model_catalog, "native_executable", lambda name: f"/bin/{name}")
    monkeypatch.setattr(model_catalog, "_run", run)
    first = model_catalog.provider_models()
    assert first["codex"]["source"] == "cli"
    assert first["codex"]["efforts"] == ["low", "high", "medium"]
    assert [m["id"] for m in first["claude"]["models"]] == ["fable", "opus", "sonnet"]
    model_catalog.provider_models()
    assert len(calls) == 2                      # the second call used the cache
    model_catalog.provider_models(refresh=True)
    assert len(calls) == 4


def test_a_missing_or_unreadable_cli_falls_back_to_a_built_in_list_and_says_so(monkeypatch):
    def gone(name):
        raise BridgeError("not installed")

    monkeypatch.setattr(model_catalog, "native_executable", gone)
    result = model_catalog.provider_models()
    assert result["codex"]["source"] == "builtin" and result["codex"]["reason"] == "not_installed"
    assert result["codex"]["models"] == []       # no list to offer: the console shows a text box
    assert [m["id"] for m in result["claude"]["models"]] == ["opus", "sonnet", "haiku"]

    model_catalog._cache.clear()
    monkeypatch.setattr(model_catalog, "native_executable", lambda name: f"/bin/{name}")
    monkeypatch.setattr(model_catalog, "_run", lambda argv: "garbage")
    assert model_catalog.provider_models()["claude"]["source"] == "builtin"


def test_the_console_serves_the_catalog_to_the_session_only(tmp_path, monkeypatch):
    seen = []
    monkeypatch.setattr("ccc_mcp.interfaces.web.provider_models",
                        lambda refresh: seen.append(refresh) or {"codex": {"models": []}})
    app = create_app(tmp_path / "home", token=TOKEN)
    anonymous = TestClient(app)
    client = TestClient(app, headers={"X-CCC-UI-Token": TOKEN})
    assert anonymous.get("/api/provider-models").status_code == 401
    assert client.get("/api/provider-models").json() == {"providers": {"codex": {"models": []}}}
    client.get("/api/provider-models?refresh=1")
    assert seen == [False, True]


def test_the_defaults_come_from_the_users_own_cli_settings(tmp_path, monkeypatch):
    monkeypatch.setattr(model_catalog.Path, "home", classmethod(lambda cls: tmp_path))
    assert model_catalog.configured_defaults("codex") == {"model": "", "effort": ""}
    (tmp_path / ".codex").mkdir()
    (tmp_path / ".codex" / "config.toml").write_text(
        'model = "gpt-x"\nmodel_reasoning_effort = "high"\n', encoding="utf-8")
    (tmp_path / ".claude").mkdir()
    (tmp_path / ".claude" / "settings.json").write_text('{"model": "opus"}', encoding="utf-8")
    assert model_catalog.configured_defaults("codex") == {"model": "gpt-x", "effort": "high"}
    assert model_catalog.configured_defaults("claude") == {"model": "opus", "effort": ""}
    (tmp_path / ".codex" / "config.toml").write_text("not = [valid", encoding="utf-8")
    assert model_catalog.configured_defaults("codex") == {"model": "", "effort": ""}
