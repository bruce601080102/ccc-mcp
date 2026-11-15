"""Stable node identity across GraphVersions. Similarity alone never creates identity.

A candidate pair (old symbol, new symbol) is considered when it is plausible (score >= 0.5) or
when a human confirmed it. It becomes an automatic identity only when every check passes:

  1. Score >= 0.8.
  2. Unique: no other unmatched candidate on either side is plausible for the same symbol.
  3. Parent evidence, by kind:
       methods            the parent class must already carry the same identity: the same key, or
                          an accepted class pair earlier in this run (classes are resolved first).
       functions/classes  one of: same file; the same name with an identical body and signature;
                          or file-level evidence (the old file is gone from the current snapshot and
                          the new file did not exist before, i.e. a file rename or split).

Any failed check makes the pair PENDING_CONFIRMATION, with the reason codes recorded on the event.
A pending pair never receives the old identity, and the old node stays in history as REMOVED.
A human "confirm" is recorded as CONFIRMED_BY_USER; a human "reject" keeps the pair apart.
"""

from .graph import Node

AUTO_THRESHOLD = 0.8
PENDING_THRESHOLD = 0.5


def jaccard(left, right):
    if not left and not right:
        return 1.0
    union = left | right
    return len(left & right) / len(union) if union else 0.0


def _name(qualname):
    return qualname.rpartition(".")[2]


def _parent_key(node):
    parent = node.qualname.rpartition(".")[0]
    return f"{node.path}::{parent}" if parent else None


def _score(old, new):
    if not old.tokens and not new.tokens:
        # Bodies with no names or literals (e.g. empty classes) carry no evidence.
        return 0.0
    if old.body_hash == new.body_hash and old.signature_hash == new.signature_hash:
        return 1.0
    return jaccard(set(old.tokens), set(new.tokens))


def _move_type(old_key, new_key):
    old_path, _, old_name = old_key.partition("::")
    new_path, _, new_name = new_key.partition("::")
    if old_path != new_path and old_name != new_name:
        return "MOVE_RENAME"
    if old_path != new_path:
        return "MOVE"
    return "RENAME"


def assign_identities(prev_nodes, current, confirmations, counter):
    """Assign `nid` to every node of `current`. Returns (events, new_counter).

    `prev_nodes` are the ACTIVE nodes of the previous GraphVersion. `confirmations` maps
    (old_key, new_key) -> "confirm" | "reject". `current.files` supplies file-level evidence.
    """
    events = []
    next_counter = counter

    def new_nid():
        nonlocal next_counter
        next_counter += 1
        return f"N-{next_counter:04d}"

    prev_by_key = {p.key: p for p in prev_nodes}
    cur_by_key = {c.key: c for c in current.nodes}
    resolved = {}  # old key -> new key, for every pair that carries identity in this run
    matched_prev = set()
    for node in current.nodes:
        prev = prev_by_key.get(node.key)
        if prev is not None and prev.kind == node.kind:
            node.nid = prev.nid
            matched_prev.add(prev.key)
            resolved[prev.key] = node.key

    prev_paths = {p.path for p in prev_nodes}

    def file_gone(path):
        return current.files.get(path, {}).get("blob") is None

    pool_prev = [p for p in prev_nodes if p.key not in matched_prev and p.key not in cur_by_key]
    unmatched_new = [n for n in current.nodes if n.nid is None]
    candidates = []
    for new in unmatched_new:
        for old in pool_prev:
            if old.kind != new.kind:
                continue
            score = _score(old, new)
            confirmed = confirmations.get((old.key, new.key)) == "confirm"
            if score >= PENDING_THRESHOLD or confirmed:
                candidates.append((score, old, new))
    # Classes first, so that a method can inherit identity through its parent class.
    candidates.sort(key=lambda c: (c[2].kind != "class", -c[0], c[1].key, c[2].key))

    taken_old, taken_new = set(), set()

    def accept(old, new, score, status, reasons):
        new.nid = old.nid
        taken_old.add(old.key)
        taken_new.add(new.key)
        resolved[old.key] = new.key
        events.append(
            {
                "type": _move_type(old.key, new.key),
                "nid": old.nid,
                "from_key": old.key,
                "to_key": new.key,
                "score": round(score, 3),
                "status": status,
                "reasons": reasons,
            }
        )

    for score, old, new in candidates:
        if old.key in taken_old or new.key in taken_new:
            continue
        decision = confirmations.get((old.key, new.key))
        if decision == "reject":
            continue
        if decision == "confirm":
            accept(old, new, score, "CONFIRMED_BY_USER", ["HUMAN_CONFIRMED"])
            continue

        blocking = []
        rivals = [
            1
            for s, o, n in candidates
            if o.key not in taken_old
            and n.key not in taken_new
            and ((n.key == new.key and o.key != old.key) or (o.key == old.key and n.key != new.key))
        ]
        if rivals:
            blocking.append("AMBIGUOUS_CANDIDATES")
        if score < AUTO_THRESHOLD:
            blocking.append("SCORE_BELOW_AUTO")

        evidence = []
        if new.kind == "method":
            if resolved.get(_parent_key(old)) == _parent_key(new):
                evidence.append("PARENT_IDENTITY_RESOLVED")
            else:
                blocking.append("PARENT_IDENTITY_UNRESOLVED")
        elif old.path == new.path:
            evidence.append("SAME_FILE")
        elif (
            _name(old.qualname) == _name(new.qualname)
            and old.body_hash == new.body_hash
            and old.signature_hash == new.signature_hash
        ):
            evidence.append("UNIQUE_IDENTICAL_NAME_AND_BODY")
        elif file_gone(old.path) and new.path not in prev_paths:
            evidence.append("FILE_LEVEL_EVIDENCE")
        else:
            blocking.append("NO_FILE_OR_NAME_EVIDENCE")

        if blocking:
            events.append(
                {
                    "type": "PENDING_CONFIRMATION",
                    "nid": None,
                    "candidate_nid": old.nid,
                    "from_key": old.key,
                    "to_key": new.key,
                    "score": round(score, 3),
                    "status": "PENDING",
                    "reasons": blocking,
                }
            )
        else:
            accept(old, new, score, "AUTO", evidence)

    for node in current.nodes:
        if node.nid is None:
            node.nid = new_nid()
            events.append(
                {
                    "type": "NEW",
                    "nid": node.nid,
                    "from_key": None,
                    "to_key": node.key,
                    "score": None,
                    "status": "AUTO",
                    "reasons": [],
                }
            )

    for old in prev_nodes:
        if old.key not in matched_prev and old.key not in taken_old and old.key not in cur_by_key:
            current.removed.append(Node(**{**old.as_dict(), "status": "REMOVED"}))
            events.append(
                {
                    "type": "REMOVE",
                    "nid": old.nid,
                    "from_key": old.key,
                    "to_key": None,
                    "score": None,
                    "status": "AUTO",
                    "reasons": [],
                }
            )
    current.events = events
    return events, next_counter
