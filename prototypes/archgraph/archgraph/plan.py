"""PlannedGraph: the approved plan for one task, as structured input.

The plan names features (what the user sees), symbols (keys path::qualname with an action),
and files (what the plan intends to touch). Planned nodes reference real keys only; the
prototype never invents identities for planned code.
"""

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path

from .graph import canonical_json

ACTIONS = ("ADD", "MODIFY", "REMOVE")
FILE_LISTS = ("add", "modify", "delete", "affected")


class PlanError(ValueError):
    pass


@dataclass
class PlannedGraph:
    gv_id: str
    task: str
    title: str
    features: list
    symbols: list
    files: dict
    module_titles: dict = field(default_factory=dict)
    planned_dependencies: list | None = None  # None: the plan does not state dependencies
    doc: dict = field(repr=False, default_factory=dict)


def load_plan(source):
    doc = (
        json.loads(Path(source).read_text(encoding="utf-8"))
        if isinstance(source, (str, Path))
        else source
    )
    if not isinstance(doc, dict) or not isinstance(doc.get("task"), str):
        raise PlanError("plan must be an object with a string 'task'")
    features = doc.get("features", [])
    seen = set()
    for feature in features:
        if not isinstance(feature, dict) or not isinstance(feature.get("id"), str):
            raise PlanError("each feature needs a string id")
        if feature["id"] in seen:
            raise PlanError(f"duplicate feature id {feature['id']}")
        seen.add(feature["id"])
        if not isinstance(feature.get("title_plain"), str):
            raise PlanError(f"feature {feature['id']} needs title_plain")
        members = feature.get("members", [])
        if not isinstance(members, list) or not all(
            isinstance(m, str) and "::" in m for m in members
        ):
            raise PlanError(f"feature {feature['id']} members must be path::qualname keys")
    symbols = doc.get("symbols", [])
    for symbol in symbols:
        if (
            not isinstance(symbol, dict)
            or not isinstance(symbol.get("key"), str)
            or "::" not in symbol["key"]
        ):
            raise PlanError("each symbol needs a path::qualname key")
        if symbol.get("action") not in ACTIONS:
            raise PlanError(f"symbol action must be one of {ACTIONS}")
    files = doc.get("files", {})
    if not isinstance(files, dict) or not set(files) <= set(FILE_LISTS):
        raise PlanError(f"files may only contain {FILE_LISTS}")
    for name, paths in files.items():
        if not isinstance(paths, list) or not all(isinstance(p, str) for p in paths):
            raise PlanError(f"files.{name} must be a list of paths")
    titles = doc.get("module_titles", {})
    if not isinstance(titles, dict) or not all(
        isinstance(k, str) and isinstance(v, str) for k, v in titles.items()
    ):
        raise PlanError("module_titles must map file paths to plain-language titles")
    dependencies = doc.get("planned_dependencies")
    if dependencies is not None and not (
        isinstance(dependencies, list)
        and all(
            isinstance(pair, list) and len(pair) == 2 and all(isinstance(p, str) for p in pair)
            for pair in dependencies
        )
    ):
        raise PlanError("planned_dependencies must be a list of [importer, imported] file pairs")
    gv_id = hashlib.sha256(canonical_json(doc).encode()).hexdigest()[:16]
    return PlannedGraph(
        gv_id=gv_id,
        task=doc["task"],
        title=doc.get("title", ""),
        features=features,
        symbols=symbols,
        files={name: list(files.get(name, [])) for name in FILE_LISTS},
        module_titles=dict(titles),
        planned_dependencies=None if dependencies is None else [list(p) for p in dependencies],
        doc=doc,
    )
