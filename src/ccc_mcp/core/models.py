from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

# Bump only for incompatible changes to the published schemas (docs/schema/*.json);
# an additive, optional field does not require a bump. See docs/en/9-protocol.md.
PROTOCOL_VERSION = "1"


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


def strict_json_schema(model: type[BaseModel]) -> dict:
    """Pydantic schema with additionalProperties:false and required everywhere.

    Used both to constrain real Agent CLI structured output and to publish a
    stable external contract for the models external consumers (docs/schema/*.json).
    """
    schema = model.model_json_schema()

    def tighten(node):
        if isinstance(node, dict):
            if node.get("type") == "object":
                node["additionalProperties"] = False
                node["required"] = list(node.get("properties", {}))
            for value in node.values():
                tighten(value)
        elif isinstance(node, list):
            for value in node:
                tighten(value)

    tighten(schema)
    return schema


class SourceRef(StrictModel):
    path: str = Field(min_length=1)
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


class Invariant(StrictModel):
    id: str = Field(min_length=1)
    kind: Literal["requirement", "acceptance", "constraint", "blocker"]
    text: str = Field(min_length=1)


class Fact(StrictModel):
    text: str = Field(min_length=1)
    source_document_refs: list[SourceRef] = Field(min_length=1)


class AgentContextResult(StrictModel):
    execution_id: str = Field(min_length=1)
    task_id: str = Field(min_length=1)
    provider: str = Field(min_length=1)
    role: str = Field(min_length=1)
    base_context_version: int = Field(ge=1)
    summary: str
    findings: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    next_action: str
    context_updates: list[str] = Field(default_factory=list)
    source_document_ref: SourceRef
    review_status: Literal["unreviewed"] = "unreviewed"


class WorkspaceChange(StrictModel):
    edit_id: str = Field(min_length=1)
    path: str = Field(min_length=1)
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


class ContextSnapshot(StrictModel):
    project_name: str = Field(min_length=1)
    purpose: str = Field(min_length=1)
    status: str = "Initialized"
    next_action: str = "建立需求與驗收條件，指派 SA 分析。"
    summary: str = "尚無 Agent 成果。"
    unresolved_questions: list[str] = Field(default_factory=list)
    invariants: list[Invariant] = Field(default_factory=list)
    architecture: list[Fact] = Field(default_factory=list)
    source_document_refs: list[SourceRef] = Field(default_factory=list)
    agent_results: list[AgentContextResult] = Field(default_factory=list)
    workspace_changes: list[WorkspaceChange] = Field(default_factory=list)


class ContextPatch(StrictModel):
    base_context_version: int = Field(ge=1)
    changes: dict = Field(min_length=1)
    source_task_ids: list[str] = Field(default_factory=list)
    source_execution_ids: list[str] = Field(default_factory=list)
    source_document_refs: list[SourceRef] = Field(default_factory=list)
    update_reason: str = Field(min_length=1)
    author_role: Literal["PM"] = "PM"
    semantic_review_ack: bool = False


class TestClaim(StrictModel):
    name: str
    claimed_result: str
    details: str


class AgentReport(StrictModel):
    summary: str
    findings: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    next_action: str
    tests: list[TestClaim] = Field(default_factory=list)
    needs_revision: bool = False
    simulated: bool = False
    context_updates: list[str] = Field(default_factory=list)
