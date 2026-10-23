import json
from pathlib import Path

from typer.testing import CliRunner

from ccc_mcp.core.models import (
    AgentReport,
    ClarificationRequest,
    ClarificationResponse,
    ContextPatch,
    ContextSnapshot,
    strict_json_schema,
)
from ccc_mcp.interfaces.cli import app

SCHEMA_DIR = Path(__file__).resolve().parents[1] / "docs" / "schema"
MODELS = {
    "agent-report": AgentReport,
    "context-snapshot": ContextSnapshot,
    "context-patch": ContextPatch,
    "clarification-request": ClarificationRequest,
    "clarification-response": ClarificationResponse,
}


def test_published_schema_files_match_current_models():
    for name, model in MODELS.items():
        published = json.loads((SCHEMA_DIR / f"{name}.json").read_text(encoding="utf-8"))
        current = strict_json_schema(model)
        assert published == current, (
            f"docs/schema/{name}.json is stale; regenerate with "
            "`ccc-mcp schema export docs/schema`"
        )


def test_schema_export_writes_every_protocol_model(tmp_path):
    runner = CliRunner()
    out_dir = tmp_path / "schema"
    result = runner.invoke(app, ["--home", str(tmp_path / "home"), "schema", "export", str(out_dir)])
    assert result.exit_code == 0, result.output

    for name, model in MODELS.items():
        written = json.loads((out_dir / f"{name}.json").read_text(encoding="utf-8"))
        assert written == strict_json_schema(model)
