"""ActualGraph: a GraphVersion built from a snapshot's indexable Python files.

A GraphVersion holds structure only: file states, symbol nodes with identity (nid) and hashes,
and IMPORT / CONTAINS edges. It never holds source text, excluded content, or secrets.
"""

import hashlib
import json
from dataclasses import asdict, dataclass, field

from .pyindex import index_python

INDEXER_VERSION = "archgraph-proto-0.1"


@dataclass
class Node:
    key: str
    path: str
    qualname: str
    kind: str
    start: int
    end: int
    signature_hash: str
    body_hash: str
    tokens: list
    provisional: bool
    nid: str | None = None
    status: str = "ACTIVE"  # ACTIVE | REMOVED
    origin: str = "STATIC_PARSE"

    def as_dict(self):
        return asdict(self)


@dataclass
class GraphVersion:
    gv_id: str
    label: str
    mode: str
    head: str | None
    snapshot_digest: str
    provisional: bool
    files: dict
    nodes: list = field(default_factory=list)
    removed: list = field(default_factory=list)
    edges: list = field(default_factory=list)
    events: list = field(default_factory=list)

    def node_map(self):
        return {node.key: node for node in self.nodes}

    def as_dict(self):
        return {
            "gv_id": self.gv_id,
            "label": self.label,
            "mode": self.mode,
            "head": self.head,
            "snapshot_digest": self.snapshot_digest,
            "provisional": self.provisional,
            "files": self.files,
            "nodes": [n.as_dict() for n in self.nodes],
            "removed": [n.as_dict() for n in self.removed],
            "edges": self.edges,
            "events": self.events,
        }

    @classmethod
    def from_dict(cls, doc):
        return cls(
            gv_id=doc["gv_id"],
            label=doc["label"],
            mode=doc["mode"],
            head=doc["head"],
            snapshot_digest=doc["snapshot_digest"],
            provisional=doc["provisional"],
            files=doc["files"],
            nodes=[Node(**n) for n in doc["nodes"]],
            removed=[Node(**n) for n in doc["removed"]],
            edges=doc["edges"],
            events=doc["events"],
        )


def empty_graph(label="empty"):
    return GraphVersion(
        gv_id=hashlib.sha256(b"empty-graph").hexdigest()[:16],
        label=label,
        mode="commit",
        head=None,
        snapshot_digest="empty",
        provisional=False,
        files={},
    )


def module_name(path):
    stem = path[:-3] if path.endswith(".py") else path
    parts = stem.split("/")
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def resolve_import(path, level, module, names):
    """Map an import to a project file, or None when it points outside the project."""
    if level:
        package = path.split("/")[:-1]
        if level > 1:
            package = package[: len(package) - (level - 1)]
        target = ".".join(package + ([module] if module else []))
    else:
        target = module
    candidate = target
    while candidate:
        if candidate in names:
            return names[candidate]
        candidate = candidate.rpartition(".")[0]
    return None


def build_actual(snapshot, contents, label):
    files = {}
    for state in snapshot.files:
        files[state.path] = {
            "blob": state.compare_blob,
            "index_blob": state.index_blob,
            "worktree_blob": state.worktree_blob,
            "observed": state.read,
            "excluded": state.excluded,
            "change": {"index": state.index_change, "worktree": state.worktree_change},
        }

    names = {module_name(path): path for path in contents}
    nodes, imports = [], {}
    for path in sorted(contents):
        result = index_python(path, contents[path])
        if result["parse_error"]:
            files[path]["parse_error"] = result["parse_error"]
        imports[path] = result["imports"]
        for symbol in result["symbols"]:
            nodes.append(
                Node(
                    key=symbol.key,
                    path=path,
                    qualname=symbol.qualname,
                    kind=symbol.kind,
                    start=symbol.start,
                    end=symbol.end,
                    signature_hash=symbol.signature_hash,
                    body_hash=symbol.body_hash,
                    tokens=list(symbol.tokens),
                    provisional=snapshot.mode == "worktree",
                )
            )

    edges = set()
    for path in sorted(contents):
        for level, module in imports[path]:
            target = resolve_import(path, level, module, names)
            if target and target != path:
                edges.add(("IMPORT", path, target))
    for node in nodes:
        edges.add(("CONTAINS", node.path, node.key))

    digest = snapshot.digest()
    gv_id = hashlib.sha256(f"{INDEXER_VERSION}|{digest}".encode()).hexdigest()[:16]
    return GraphVersion(
        gv_id=gv_id,
        label=label,
        mode=snapshot.mode,
        head=snapshot.head,
        snapshot_digest=digest,
        provisional=snapshot.mode == "worktree",
        files=files,
        nodes=nodes,
        edges=[{"kind": k, "src": s, "dst": d} for k, s, d in sorted(edges)],
    )


def canonical_json(doc):
    return json.dumps(doc, sort_keys=True, ensure_ascii=False)
