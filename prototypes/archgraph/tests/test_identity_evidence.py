"""Identity must rest on evidence. Same __init__ or same body alone never merges automatically."""

from archgraph.graph import Node
from archgraph.identity import assign_identities


def _node(key, kind="function", body="b", sig="s", tokens=("a", "b", "c"), nid=None):
    path, _, qual = key.partition("::")
    node = Node(
        key=key,
        path=path,
        qualname=qual,
        kind=kind,
        start=1,
        end=2,
        signature_hash=sig,
        body_hash=body,
        tokens=list(tokens),
        provisional=False,
    )
    node.nid = nid
    return node


class _Current:
    def __init__(self, nodes, files):
        self.nodes = nodes
        self.removed = []
        self.events = []
        self.files = files


def _files(*paths):
    return {
        p: {"blob": "x", "excluded": None, "change": {"index": "none", "worktree": "none"}}
        for p in paths
    }


def _events_of(events, type_):
    return [e for e in events if e["type"] == type_]


def test_identical_init_in_different_classes_is_never_auto_merged():
    prev = [
        _node("m.py::A", kind="class", tokens=(), nid="N-0001"),
        _node("m.py::A.__init__", kind="method", tokens=("self", "path"), nid="N-0002"),
    ]
    cur = _Current(
        [
            _node("m.py::B", kind="class", tokens=()),
            _node("m.py::B.__init__", kind="method", tokens=("self", "path")),
        ],
        _files("m.py"),
    )
    events, _ = assign_identities(prev, cur, {}, 2)
    assert _events_of(events, "MOVE") == [] and _events_of(events, "MOVE_RENAME") == []
    pending = _events_of(events, "PENDING_CONFIRMATION")
    init_pending = [e for e in pending if e["to_key"] == "m.py::B.__init__"]
    assert len(init_pending) == 1
    assert "PARENT_IDENTITY_UNRESOLVED" in init_pending[0]["reasons"]
    new_init = next(n for n in cur.nodes if n.key == "m.py::B.__init__")
    assert new_init.nid != "N-0002"


def test_identical_init_moves_only_after_its_parent_class_is_confirmed():
    prev = [
        _node("m.py::A", kind="class", tokens=(), nid="N-0001"),
        _node("m.py::A.__init__", kind="method", tokens=("self", "path"), nid="N-0002"),
    ]
    cur = _Current(
        [
            _node("m.py::B", kind="class", tokens=()),
            _node("m.py::B.__init__", kind="method", tokens=("self", "path")),
        ],
        _files("m.py"),
    )
    decisions = {("m.py::A", "m.py::B"): "confirm"}
    events, _ = assign_identities(prev, cur, decisions, 2)
    class_event = next(e for e in events if e["to_key"] == "m.py::B")
    assert class_event["status"] == "CONFIRMED_BY_USER"
    new_init = next(n for n in cur.nodes if n.key == "m.py::B.__init__")
    assert new_init.nid == "N-0002"  # the child keeps identity once its parent is resolved
    init_event = next(e for e in events if e["to_key"] == "m.py::B.__init__")
    assert init_event["status"] == "AUTO"
    assert "PARENT_IDENTITY_RESOLVED" in init_event["reasons"]


def test_truly_moved_function_keeps_its_identity_with_unique_evidence():
    prev = [
        _node("a.py::helper", body="h", nid="N-0007", tokens=("x", "y", "z")),
        _node("a.py::other", body="o", nid="N-0008", tokens=("p", "q")),
    ]
    cur = _Current(
        [
            _node("a.py::other", body="o", tokens=("p", "q")),
            _node("b.py::helper", body="h", tokens=("x", "y", "z")),
        ],
        _files("a.py", "b.py"),
    )
    events, _ = assign_identities(prev, cur, {}, 8)
    moved = next(e for e in events if e["to_key"] == "b.py::helper")
    assert moved["type"] == "MOVE" and moved["status"] == "AUTO"
    assert moved["nid"] == "N-0007"
    assert "UNIQUE_IDENTICAL_NAME_AND_BODY" in moved["reasons"]


def test_moved_and_renamed_function_is_auto_only_with_file_level_evidence():
    prev = [_node("a.py::helper", body="h1", tokens=("x", "y", "z", "w"), nid="N-0007")]
    cur = _Current(
        [_node("b.py::helper2", body="h2", tokens=("x", "y", "z", "w"))],
        _files("b.py"),  # a.py is gone from the current snapshot: a file-level move
    )
    events, _ = assign_identities(prev, cur, {}, 7)
    moved = next(e for e in events if e["to_key"] == "b.py::helper2")
    assert moved["status"] == "AUTO" and moved["nid"] == "N-0007"
    assert "FILE_LEVEL_EVIDENCE" in moved["reasons"]


def test_rename_and_move_without_file_or_name_evidence_stays_pending():
    prev = [_node("a.py::helper", body="h", tokens=("x", "y", "z"), nid="N-0007")]
    cur = _Current(
        [
            _node("a.py::keep", body="k", tokens=("u",)),
            _node("b.py::h2", body="h", tokens=("x", "y", "z")),
        ],
        _files("a.py", "b.py"),  # a.py still exists and b.py is new, but names differ
    )
    events, _ = assign_identities(prev, cur, {}, 7)
    pending = next(e for e in events if e["type"] == "PENDING_CONFIRMATION")
    assert pending["to_key"] == "b.py::h2"
    assert "NO_FILE_OR_NAME_EVIDENCE" in pending["reasons"]
    assert next(n for n in cur.nodes if n.key == "b.py::h2").nid != "N-0007"


def test_several_identical_candidates_with_the_same_score_are_all_pending():
    prev = [
        _node(
            "a.py::A.__init__", kind="method", body="same", tokens=("self", "path"), nid="N-0001"
        ),
        _node(
            "b.py::B.__init__", kind="method", body="same", tokens=("self", "path"), nid="N-0002"
        ),
    ]
    cur = _Current(
        [_node("c.py::C.__init__", kind="method", body="same", tokens=("self", "path"))],
        _files("c.py"),
    )
    events, _ = assign_identities(prev, cur, {}, 2)
    assert _events_of(events, "MOVE") == [] and _events_of(events, "MOVE_RENAME") == []
    pending = _events_of(events, "PENDING_CONFIRMATION")
    assert sorted(e["candidate_nid"] for e in pending) == ["N-0001", "N-0002"]
    assert all("AMBIGUOUS_CANDIDATES" in e["reasons"] for e in pending)
    assert cur.nodes[0].nid not in ("N-0001", "N-0002")


def test_human_rejection_keeps_the_pair_apart_with_a_reason():
    prev = [
        _node("a.py::read", body="b1", tokens=("a", "b", "c", "d", "e", "f", "g"), nid="N-0003")
    ]
    cur = _Current(
        [_node("b.py::read", body="b2", tokens=("a", "b", "c", "d", "e", "x1", "x2"))],
        _files("b.py"),
    )
    events, _ = assign_identities(prev, cur, {("a.py::read", "b.py::read"): "reject"}, 3)
    assert cur.nodes[0].nid != "N-0003"
    assert _events_of(events, "MOVE") == []
