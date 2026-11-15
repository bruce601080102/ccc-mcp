from archgraph.graph import Node
from archgraph.identity import assign_identities, jaccard
from archgraph.pyindex import index_python


def _node(key, body="x", sig="s", tokens=("a", "b", "c"), kind="function"):
    path, _, qual = key.partition("::")
    return Node(
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


class _Graph:
    def __init__(self, nodes):
        self.nodes = nodes
        self.removed = []
        self.events = []
        self.files = {}


OLD_TOKENS = ("a", "b", "c", "d", "e", "f", "g")
# 7 shared with OLD, 4 new: Jaccard = 7/11 = 0.636, inside the pending band [0.5, 0.8)
PENDING_TOKENS = ("a", "b", "c", "d", "e", "f", "g", "x1", "x2", "x3", "x4")


def test_the_pending_fixture_really_is_in_the_pending_band():
    assert 0.5 <= jaccard(set(OLD_TOKENS), set(PENDING_TOKENS)) < 0.8


def test_symbols_docstrings_and_methods_are_indexed_structure_only():
    source = (
        b'"""module doc"""\n'
        b"import json\nfrom .models import Note\n\n"
        b'def load(path):\n    """doc"""\n    return json.loads(path)\n\n'
        b"class Store:\n    def save(self, n):\n        return n\n"
    )
    result = index_python("pkg/store.py", source)
    assert [(s.qualname, s.kind) for s in result["symbols"]] == [
        ("load", "function"),
        ("Store", "class"),
        ("Store.save", "method"),
    ]
    assert (0, "json") in result["imports"]
    assert (1, "models") in result["imports"]
    assert "doc" not in repr(result["symbols"])  # docstrings are not carried into the index


def test_docstring_only_change_is_not_a_code_change():
    a = index_python("m.py", b'def f():\n    """one"""\n    return 1\n')["symbols"][0]
    b = index_python("m.py", b'def f():\n    """two"""\n    return 1\n')["symbols"][0]
    assert a.body_hash == b.body_hash


def test_syntax_error_is_reported_not_raised():
    result = index_python("bad.py", b"def broken(:\n")
    assert result["parse_error"] == "SyntaxError"
    assert result["symbols"] == []


def test_exact_key_keeps_identity_and_produces_no_events():
    prev = [_node("a.py::f")]
    prev[0].nid = "N-0001"
    cur = _Graph([_node("a.py::f")])
    events, counter = assign_identities(prev, cur, {}, 1)
    assert cur.nodes[0].nid == "N-0001"
    assert events == []
    assert counter == 1


def test_identical_body_moved_to_another_file_is_an_automatic_move():
    prev = [_node("old.py::f", body="same", sig="s", tokens=("p", "q", "r"))]
    prev[0].nid = "N-0007"
    cur = _Graph([_node("new.py::f", body="same", sig="s", tokens=("p", "q", "r"))])
    events, _ = assign_identities(prev, cur, {}, 7)
    assert cur.nodes[0].nid == "N-0007"
    assert events[0]["type"] == "MOVE"
    assert events[0]["status"] == "AUTO"


def test_uncertain_pair_is_pending_until_a_human_confirms():
    prev = [_node("old.py::read", body="b1", tokens=OLD_TOKENS)]
    prev[0].nid = "N-0003"

    pending = _Graph([_node("new.py::read", body="b2", tokens=PENDING_TOKENS)])
    events, _ = assign_identities(prev, pending, {}, 3)
    assert [e["type"] for e in events] == ["PENDING_CONFIRMATION", "NEW", "REMOVE"]
    assert pending.nodes[0].nid == "N-0004"  # never merged silently; the old node stays in history

    confirmed = _Graph([_node("new.py::read", body="b2", tokens=PENDING_TOKENS)])
    events, _ = assign_identities(prev, confirmed, {("old.py::read", "new.py::read"): "confirm"}, 3)
    assert events[0]["type"] == "MOVE"  # same name, different file
    assert events[0]["status"] == "CONFIRMED_BY_USER"
    assert confirmed.nodes[0].nid == "N-0003"


def test_rejected_pair_stays_apart_and_old_node_is_removed_not_erased():
    prev = [_node("old.py::read", body="b1", tokens=OLD_TOKENS)]
    prev[0].nid = "N-0003"
    cur = _Graph([_node("new.py::read", body="b2", tokens=PENDING_TOKENS)])
    events, _ = assign_identities(prev, cur, {("old.py::read", "new.py::read"): "reject"}, 3)
    assert cur.nodes[0].nid == "N-0004"
    assert any(e["type"] == "REMOVE" and e["nid"] == "N-0003" for e in events)
    assert [r.nid for r in cur.removed] == ["N-0003"]
    assert cur.removed[0].status == "REMOVED"


def test_two_empty_classes_are_never_merged_on_shape_alone():
    prev = [_node("store.py::NotesStore", body="e", sig="s", tokens=(), kind="class")]
    prev[0].nid = "N-0001"
    cur = _Graph([_node("migrate.py::JsonImporter", body="e", sig="s", tokens=(), kind="class")])
    events, _ = assign_identities(prev, cur, {}, 1)
    assert cur.nodes[0].nid == "N-0002"
    assert any(e["type"] == "REMOVE" and e["nid"] == "N-0001" for e in events)


def test_identical_copies_are_ambiguous_and_never_merged_by_order():
    # Two old __init__ methods with the same body; the new file has one copy of that body.
    prev = [
        _node("a.py::A.__init__", body="same", sig="s", tokens=("self", "path")),
        _node("b.py::B.__init__", body="same", sig="s", tokens=("self", "path")),
    ]
    prev[0].nid, prev[1].nid = "N-0001", "N-0002"
    cur = _Graph([_node("c.py::C.__init__", body="same", sig="s", tokens=("self", "path"))])
    events, _ = assign_identities(prev, cur, {}, 2)
    pending = [e for e in events if e["type"] == "PENDING_CONFIRMATION"]
    assert sorted(e["candidate_nid"] for e in pending) == ["N-0001", "N-0002"]
    assert [e["type"] for e in events if e["type"] not in ("REMOVE", "PENDING_CONFIRMATION")] == [
        "NEW"
    ]
    assert cur.nodes[0].nid == "N-0003"  # no silent merge with either copy
    assert {e["nid"] for e in events if e["type"] == "REMOVE"} == {"N-0001", "N-0002"}
