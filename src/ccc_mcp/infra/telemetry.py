import hashlib
import json
import re
import uuid
from contextlib import contextmanager
from contextvars import ContextVar

from ..adapters.storage import now
from .errors import BridgeError
from .security import reject_secrets

ACTIVE_AUDIT = ContextVar("ccc_mcp_audit", default=None)
# Called with the pid of every process run_process spawns, right after it starts. A Host Test run
# uses it to record which child it is waiting on, so a dead runner's child can be found again.
PROCESS_OBSERVER = ContextVar("ccc_mcp_process_observer", default=None)
# File descriptors run_process lets its child inherit (POSIX). A Host Test passes its lease here, so
# the lease stays held for as long as the test process lives, even if the runner is killed.
INHERITED_FDS = ContextVar("ccc_mcp_inherited_fds", default=())

# Version of the normalised view built by canonical_usage(). Legacy `tokens` fields are
# never rewritten; the normalised view is an additional, versioned reading of them.
NORMALIZATION_VERSION = 1

# What the repository can actually support about each provider's token semantics.
#   VERIFIED     confirmed against vendor documentation or a controlled experiment (none yet)
#   OBSERVED     seen in exactly one archived real capture, not confirmed beyond it
#   UNKNOWN      no evidence either way; any value that depends on it is not exact
#   NOT_REPORTED the CLI does not emit the quantity at all
# The one OBSERVED item comes from benchmarks/toy-agents/smoke-test-evidence/
# 2026-11-10-clamp-raw-codex-stream.jsonl, where cached_input_tokens (61696) <= input_tokens (66628).
SEMANTIC_STATUS = {
    "codex": {
        "cached_input_inside_input_tokens": "OBSERVED",
        "cache_write_inside_input_tokens": "UNKNOWN",
        "reasoning_inside_output_tokens": "UNKNOWN",
        "per_turn_usage_not_cumulative": "UNKNOWN",
    },
    "claude": {
        "cache_outside_input_tokens": "UNKNOWN",
        "reasoning_reported_separately": "NOT_REPORTED",
    },
}

_CODEX_KEYS = (
    "input_tokens",
    "cached_input_tokens",
    "cache_write_input_tokens",
    "output_tokens",
    "reasoning_output_tokens",
)
_CLAUDE_KEYS = (
    "input_tokens",
    "output_tokens",
    "cache_read_input_tokens",
    "cache_creation_input_tokens",
)
_FAMILIES = ("sonnet", "opus", "haiku")
_DATE_SUFFIX = re.compile(r"-\d{8}$")


@contextmanager
def audit_scope(audit):
    token = ACTIVE_AUDIT.set(audit)
    try:
        yield
    finally:
        ACTIVE_AUDIT.reset(token)


def _valid_int(value):
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _valid_ints(mapping, keys):
    return {key: mapping[key] for key in keys if _valid_int(mapping.get(key))}


def _collect_models(node, found, depth=0):
    """Bounded search for a `model` string in one CLI event (top level and two levels down)."""
    if depth > 2 or not isinstance(node, dict):
        return
    for key, value in node.items():
        if key == "model" and isinstance(value, str) and value.strip():
            found.append(value.strip())
        elif isinstance(value, dict):
            _collect_models(value, found, depth + 1)


def usage_from_output(provider, stdout):
    """Only parse native CLI metadata, never model-authored AgentReport claims.

    `tokens` keeps the legacy shape: the raw CLI field names, summed over Codex turns.
    Meaning is added separately by canonical_usage(); nothing here relabels legacy values.
    """
    result = {"available": False, "source": "cli_output", "reported_models": []}
    if provider == "codex":
        usages, models, model_events = [], [], []
        for line in stdout.splitlines():
            try:
                item = json.loads(line)
            except ValueError:
                continue
            if not isinstance(item, dict):
                continue
            found = []
            _collect_models(item, found)
            for model in found:
                if model not in models:
                    models.append(model)
                    model_events.append(str(item.get("type", "")))
            if item.get("type") == "turn.completed" and isinstance(item.get("usage"), dict):
                usages.append(item["usage"])
        tokens = {}
        for key in _CODEX_KEYS:
            values = [u[key] for u in usages if _valid_int(u.get(key))]
            if values:
                tokens[key] = sum(values)
        result.update(reported_models=models, model_event_types=model_events, turns=len(usages))
        if usages:
            result["last_turn"] = _valid_ints(usages[-1], _CODEX_KEYS)
        if tokens:
            result.update(available=True, tokens=tokens)
    elif provider == "claude":
        try:
            envelope = json.loads(stdout)
        except ValueError:
            return result
        if not isinstance(envelope, dict):
            return result
        usage = envelope.get("usage")
        if isinstance(usage, dict):
            tokens = _valid_ints(usage, _CLAUDE_KEYS)
            if tokens:
                result.update(available=True, tokens=tokens, turns=1)
        models = envelope.get("modelUsage", {})
        if isinstance(models, dict):
            result["reported_models"] = list(models)
        cost = envelope.get("total_cost_usd")
        if isinstance(cost, (int, float)) and not isinstance(cost, bool) and cost >= 0:
            result["cli_estimated_cost_usd"] = cost
            result["cost_note"] = "CLI estimate; not a subscription invoice or remaining quota"
    return result


def _canonical_empty(provider, flags):
    return {
        "normalization_version": NORMALIZATION_VERSION,
        "provider": provider,
        "status": "UNAVAILABLE",
        "fields": {
            "input_uncached": None,
            "cache_read": None,
            "cache_write": None,
            "output_visible": None,
            "reasoning": None,
        },
        "total_assumed": None,
        "total_range": None,
        "total_status": "UNAVAILABLE",
        "components_sum": None,
        "turns": 0,
        "unknown_semantics": [],
        "observed_semantics": [],
        "semantics": dict(SEMANTIC_STATUS.get(provider, {})),
        "flags": flags,
    }


def _finish(
    provider, fields, required, assumed, bounds, total_status, flags, unknown, observed, turns
):
    flags = list(dict.fromkeys(flags))
    conflicts = [flag for flag in flags if flag.startswith("CONFLICT")]
    if conflicts and total_status != "UNAVAILABLE":
        total_status = "UNVERIFIED"
    if conflicts:
        status = "CONFLICT"
    elif all(fields[name] is not None for name in required):
        status = "COMPLETE"
    else:
        status = "PARTIAL"
    components = None
    if all(fields[name] is not None for name in required):
        components = sum(value for value in fields.values() if value is not None)
    return {
        "normalization_version": NORMALIZATION_VERSION,
        "provider": provider,
        "status": status,
        "fields": fields,
        "total_assumed": assumed,
        "total_range": bounds,
        "total_status": total_status,
        "components_sum": components,
        "turns": turns,
        "unknown_semantics": unknown,
        "observed_semantics": observed,
        "semantics": dict(SEMANTIC_STATUS[provider]),
        "flags": flags,
    }


def _canonical_codex(tokens, turns, last_turn):
    flags = []
    total_in = tokens.get("input_tokens")
    total_out = tokens.get("output_tokens")
    cache_read = tokens.get("cached_input_tokens")
    cache_write = tokens.get("cache_write_input_tokens")
    reasoning = tokens.get("reasoning_output_tokens")
    fields = {
        "input_uncached": None,
        "cache_read": cache_read,
        "cache_write": cache_write,
        "output_visible": None,
        "reasoning": reasoning,
    }
    # cached_input_tokens is a subset of input_tokens (observed). Subtract it once, never add it.
    if total_in is not None and cache_read is not None:
        if cache_read > total_in:
            flags.append("CONFLICT_CACHE_READ_EXCEEDS_INPUT")
        elif cache_write is None:
            flags.append("RESULT_FIELD_ABSENT:cache_write_input_tokens")
        elif cache_read + cache_write > total_in:
            flags.append("CONFLICT_CACHE_EXCEEDS_INPUT")
        else:
            fields["input_uncached"] = total_in - cache_read - cache_write
    else:
        flags.append("RESULT_FIELD_ABSENT:input_tokens_or_cached_input_tokens")
    # reasoning_output_tokens is treated as a subset of output_tokens (UNKNOWN); it is
    # subtracted from output here so that it is never counted twice.
    if total_out is not None and reasoning is not None:
        if reasoning > total_out:
            flags.append("CONFLICT_REASONING_EXCEEDS_OUTPUT")
        else:
            fields["output_visible"] = total_out - reasoning
    else:
        flags.append("RESULT_FIELD_ABSENT:output_tokens_or_reasoning_output_tokens")

    assumed = bounds = None
    unknown, observed = [], []
    if total_in is not None and total_out is not None:
        base = total_in + total_out
        assumed = base
        low, high = base, base
        if cache_write is None or reasoning is None:
            high = None
            if cache_write is None:
                flags.append("RESULT_FIELD_ABSENT:cache_write_input_tokens")
            if reasoning is None:
                flags.append("RESULT_FIELD_ABSENT:reasoning_output_tokens")
        else:
            # If cache_write or reasoning sat outside the totals they are reported beside,
            # the true total would be larger. That is the upper bound.
            high = base + cache_write + reasoning
        if cache_write:
            unknown.append("cache_write_inside_input_tokens")
        if reasoning:
            unknown.append("reasoning_inside_output_tokens")
        if turns > 1 and last_turn:
            alt = (last_turn.get("input_tokens") or 0) + (last_turn.get("output_tokens") or 0)
            low = min(low, alt)
            if high is not None:
                high = max(high, alt)
            unknown.append("per_turn_usage_not_cumulative")
            flags.append("TURN_SEMANTICS_UNKNOWN")
        bounds = [low, high]
    if cache_read:
        observed.append("cached_input_inside_input_tokens")

    if assumed is None:
        total_status = "UNAVAILABLE"
    elif bounds[1] is None or unknown:
        total_status = "UNVERIFIED"
    elif observed:
        total_status = "OBSERVED_ONLY"
    else:
        total_status = "DETERMINED"
    required = ("input_uncached", "cache_read", "cache_write", "output_visible", "reasoning")
    return _finish(
        "codex", fields, required, assumed, bounds, total_status, flags, unknown, observed, turns
    )


def _canonical_claude(tokens):
    flags = ["REASONING_NOT_REPORTED_SEPARATELY"]
    total_in = tokens.get("input_tokens")
    total_out = tokens.get("output_tokens")
    cache_read = tokens.get("cache_read_input_tokens")
    cache_write = tokens.get("cache_creation_input_tokens")
    fields = {
        "input_uncached": total_in,
        "cache_read": cache_read,
        "cache_write": cache_write,
        "output_visible": total_out,
        "reasoning": None,
    }
    assumed = bounds = None
    unknown, observed = [], []
    if total_in is None or total_out is None:
        total_status = "UNAVAILABLE"
        flags.append("RESULT_FIELD_ABSENT:input_tokens_or_output_tokens")
    else:
        low = total_in + total_out
        if cache_read is None or cache_write is None:
            flags.append("RESULT_FIELD_ABSENT:cache_read_or_cache_creation_input_tokens")
            bounds = [low, None]
            total_status = "UNVERIFIED"
        else:
            # Anthropic's convention is that input_tokens excludes cache. That is UNKNOWN
            # here: no real capture in this repository confirms it, so the upper bound is
            # reported as the assumed total and the lower bound is kept beside it.
            high = total_in + cache_read + cache_write + total_out
            assumed = high
            bounds = [low, high]
            if cache_read or cache_write:
                unknown.append("cache_outside_input_tokens")
                total_status = "UNVERIFIED"
            else:
                total_status = "DETERMINED"
    required = ("input_uncached", "cache_read", "cache_write", "output_visible")
    return _finish(
        "claude", fields, required, assumed, bounds, total_status, flags, unknown, observed, 1
    )


def canonical_usage(provider, tokens, turns=1, last_turn=None):
    """Normalise one execution's native usage without double counting.

    Input side:  input_uncached + cache_read + cache_write  (cache is a subset of the input
                 total for Codex, observed; a separate addend for Claude, UNKNOWN)
    Output side: output_visible + reasoning                 (reasoning is a subset of output
                 for Codex, UNKNOWN; not reported at all for Claude)
    A total is never a sum of raw fields. `total_range` gives the bounds implied by the
    UNKNOWN placements; `total_status` says how much of the value rests on evidence.
    A value that cannot be determined is None, never 0.
    """
    if provider not in SEMANTIC_STATUS:
        return _canonical_empty(provider, ["UNSUPPORTED_PROVIDER"])
    if not isinstance(tokens, dict) or not tokens:
        return _canonical_empty(provider, ["NO_USAGE_RECORDED"])
    if provider == "codex":
        return _canonical_codex(tokens, turns, last_turn or {})
    return _canonical_claude(tokens)


def _normalize_model(name):
    return _DATE_SUFFIX.sub("", name.strip().lower())


def model_identity(provider, requested, reported):
    """Compare the model that was asked for with the model names the CLI reported.

    MATCH     the requested model is among the reported models (exact, date-suffix only
              difference, or unambiguous family alias such as "sonnet").
    MISMATCH  the CLI reported models and none of them is the requested one.
    UNKNOWN   nothing was requested, the CLI reported nothing, or an alias is ambiguous.
    Extra reported models are listed as `auxiliary` and never turn a MATCH into a MISMATCH.
    """
    names = [name for name in (reported or []) if isinstance(name, str) and name.strip()]
    base = {
        "provider": provider,
        "requested": requested or None,
        "reported": names,
        "auxiliary": [],
        "matched_as": None,
    }
    if not requested or not str(requested).strip():
        return {**base, "status": "UNKNOWN", "reason": "REQUEST_UNSPECIFIED"}
    if not names:
        return {**base, "status": "UNKNOWN", "reason": "CLI_REPORTED_NO_MODEL"}
    wanted = str(requested).strip().lower()
    exact = [name for name in names if name.strip().lower() == wanted]
    if exact:
        matched, how, reason = exact[0], "exact", "EXACT"
    else:
        dated = [name for name in names if _normalize_model(name) == _normalize_model(wanted)]
        family = [name for name in names if wanted in _FAMILIES and wanted in name.lower()]
        if dated:
            matched, how, reason = dated[0], "date_suffix", "DATE_SUFFIX_ONLY_DIFFERENCE"
        elif wanted in _FAMILIES and len(family) == 1:
            matched, how, reason = family[0], "alias", "ALIAS_FAMILY"
        elif wanted in _FAMILIES and len(family) > 1:
            return {**base, "status": "UNKNOWN", "reason": "AMBIGUOUS_ALIAS"}
        elif wanted in _FAMILIES:
            return {**base, "status": "MISMATCH", "reason": "ALIAS_FAMILY_ABSENT"}
        else:
            return {**base, "status": "MISMATCH", "reason": "REQUESTED_NOT_REPORTED"}
    return {
        **base,
        "status": "MATCH",
        "reason": reason,
        "matched_as": how,
        "matched": matched,
        "auxiliary": [name for name in names if name != matched],
    }


def verify_usage_against_raw(provider, stored_usage, stored_stdout_sha256, raw_stdout):
    """Check a stored usage record against the raw native output it claims to come from.

    VERIFIED only when the stored record is linked to the raw output by sha256 AND re-parsing
    the raw output reproduces the stored tokens exactly. Every other outcome is UNVERIFIED,
    with the reason and, when relevant, the differing fields. Nothing is rewritten.
    """
    if not isinstance(stored_usage, dict) or not stored_usage.get("available"):
        return {"status": "UNVERIFIED", "reason": "NO_STORED_USAGE"}
    if raw_stdout is None:
        return {"status": "UNVERIFIED", "reason": "RAW_NOT_ARCHIVED"}
    raw_sha = hashlib.sha256(raw_stdout.encode()).hexdigest()
    if stored_stdout_sha256 is not None and stored_stdout_sha256 != raw_sha:
        return {"status": "UNVERIFIED", "reason": "RAW_SHA_MISMATCH"}
    fresh = usage_from_output(provider, raw_stdout)
    if not fresh.get("available"):
        return {"status": "UNVERIFIED", "reason": "RAW_NOT_PARSEABLE"}
    stored = stored_usage.get("tokens") or {}
    raw = fresh.get("tokens") or {}
    differences = {
        key: {"stored": stored.get(key), "raw": raw.get(key)}
        for key in sorted(set(stored) | set(raw))
        if stored.get(key) != raw.get(key)
    }
    if differences:
        return {"status": "UNVERIFIED", "reason": "RAW_TOKENS_DIFFER", "differences": differences}
    if stored_stdout_sha256 is None:
        return {"status": "UNVERIFIED", "reason": "LEGACY_RECORD_NO_LINK"}
    return {"status": "VERIFIED", "reason": "SHA_LINKED_AND_TOKENS_EQUAL"}


PARSER_VERSION = "telemetry.usage-parser/1"
EVENT_SCHEMA = "ccc-mcp.usage-event/1"
_EVENT_HASH_EXCLUDED = ("usage_event_sha256", "verification")


def usage_event_hash(event):
    """SHA-256 of the event's canonical JSON, leaving out the hash and the verification verdict."""
    body = {key: value for key, value in event.items() if key not in _EVENT_HASH_EXCLUDED}
    encoded = json.dumps(body, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(encoded.encode()).hexdigest()


def build_usage_event(provider, requested_model, stdout, usage, canonical, identity, run_id):
    """A minimal record of one execution's native usage: numbers, names, and hashes only.

    It never holds the prompt, the code, or the agent's text. The total is a single value only
    when every placement it depends on is determined; otherwise only the range is given. The event
    is checked for secrets before it is returned. A suspect event is replaced by a WITHHELD marker,
    so an execution never fails because of its evidence.
    """
    total_range = canonical.get("total_range")
    single = (
        canonical.get("total_status") == "DETERMINED"
        and bool(total_range)
        and total_range[0] == total_range[1]
    )
    body = {
        "schema": EVENT_SCHEMA,
        "run_id": run_id,
        "provider": provider,
        "requested_model": requested_model or None,
        "cli_reported_models": [
            name for name in (usage.get("reported_models") or []) if isinstance(name, str)
        ],
        "identity": {
            "status": identity["status"],
            "reason": identity["reason"],
            "matched_as": identity.get("matched_as"),
        },
        "usage_source": {
            "kind": "cli_stdout",
            "shape": "none"
            if not usage.get("available")
            else ("codex.turn_completed.sum" if provider == "codex" else "claude.result.usage"),
        },
        "turns": usage.get("turns", 0),
        "raw_fields": dict(usage.get("tokens") or {}),
        "canonical": {
            "status": canonical.get("status"),
            "fields": dict(canonical.get("fields") or {}),
            "total": canonical.get("total_assumed") if single else None,
            "total_range": total_range,
            "total_status": canonical.get("total_status"),
            "unknown_semantics": list(canonical.get("unknown_semantics") or []),
        },
        "parser_version": PARSER_VERSION,
        "normalization_version": NORMALIZATION_VERSION,
        "raw_output_sha256": hashlib.sha256(stdout.encode()).hexdigest(),
    }
    body["usage_event_sha256"] = usage_event_hash(body)
    body["verification"] = {"status": "UNVERIFIED", "reason": "RAW_NOT_ARCHIVED"}
    try:
        reject_secrets(body)
    except BridgeError:
        return {
            "schema": EVENT_SCHEMA,
            "run_id": run_id,
            "provider": provider,
            "status": "WITHHELD",
            "reason": "SECRET_SUSPECT_IN_EVENT",
            "verification": {"status": "UNVERIFIED", "reason": "WITHHELD"},
        }
    return body


def verify_usage_event(event, raw_stdout):
    """VERIFIED only when the raw output is at hand, its sha256 matches the event, the event's hash
    recomputes, and re-parsing the raw output reproduces the recorded fields. Anything else is
    UNVERIFIED with the first reason that failed.
    """
    if not isinstance(event, dict) or event.get("schema") != EVENT_SCHEMA:
        return {"status": "UNVERIFIED", "reason": "NO_EVENT"}
    if event.get("status") == "WITHHELD":
        return {"status": "UNVERIFIED", "reason": "WITHHELD"}
    if usage_event_hash(event) != event.get("usage_event_sha256"):
        return {"status": "UNVERIFIED", "reason": "EVENT_HASH_MISMATCH"}
    if raw_stdout is None:
        return {"status": "UNVERIFIED", "reason": "RAW_NOT_ARCHIVED"}
    if hashlib.sha256(raw_stdout.encode()).hexdigest() != event.get("raw_output_sha256"):
        return {"status": "UNVERIFIED", "reason": "RAW_SHA_MISMATCH"}
    provider = event.get("provider")
    fresh = usage_from_output(provider, raw_stdout)
    if not fresh.get("available"):
        return {"status": "UNVERIFIED", "reason": "RAW_NOT_PARSEABLE"}
    if fresh.get("tokens") != event.get("raw_fields"):
        return {"status": "UNVERIFIED", "reason": "RAW_TOKENS_DIFFER"}
    recomputed = canonical_usage(
        provider,
        fresh["tokens"],
        turns=fresh.get("turns", 0) or 1,
        last_turn=fresh.get("last_turn"),
    )
    recorded = event.get("canonical", {})
    if recomputed["fields"] != recorded.get("fields") or recomputed["total_range"] != recorded.get(
        "total_range"
    ):
        return {"status": "UNVERIFIED", "reason": "CANONICAL_MISMATCH"}
    return {"status": "VERIFIED", "reason": "RAW_SHA_AND_TOKENS_REPRODUCED"}


def record_output(provider, stdout, stderr=""):
    audit = ACTIVE_AUDIT.get()
    if audit is not None:
        usage = usage_from_output(provider, stdout)
        requested = (audit.get("requested") or {}).get("model")
        usage["canonical"] = canonical_usage(
            provider,
            usage.get("tokens"),
            turns=usage.get("turns", 0) or 1,
            last_turn=usage.get("last_turn"),
        )
        usage["identity"] = model_identity(provider, requested, usage.get("reported_models"))
        audit["usage"] = usage
        # One run id per execution: a second recording of the same output keeps it.
        audit.setdefault("run_id", uuid.uuid4().hex)
        audit["usage_evidence"] = build_usage_event(
            provider,
            requested,
            stdout,
            usage,
            usage["canonical"],
            usage["identity"],
            audit["run_id"],
        )
        audit["stdout_sha256"] = hashlib.sha256(stdout.encode()).hexdigest()
        audit["stderr_sha256"] = hashlib.sha256(stderr.encode()).hexdigest()


def process_started(argv, cwd, pid):
    observer = PROCESS_OBSERVER.get()
    if observer is not None:
        observer(pid)
    audit = ACTIVE_AUDIT.get()
    if audit is None:
        return
    args = list(argv)
    for flag in ("--json-schema", "--output-schema"):
        if flag in args and args.index(flag) + 1 < len(args):
            args[args.index(flag) + 1] = "<schema>"
    audit.update(
        cli_invoked=True,
        executable=str(argv[0]),
        arguments=args[1:],
        cwd=str(cwd),
        pid=pid,
        started_at=now(),
    )


def process_finished(returncode):
    audit = ACTIVE_AUDIT.get()
    if audit is not None:
        audit.update(finished_at=now(), exit_code=returncode)


def is_quota_error(message):
    message = message.lower()
    return any(
        marker in message
        for marker in (
            "insufficient_quota",
            "usage_limit_reached",
            "quota_exceeded",
            "rate_limit_error",
            "quota exceeded",
            "usage limit",
            "out of credits",
            "insufficient credits",
            "credit balance",
            "you've hit your limit",
            "hit your session limit",
            "rate_limit_exceeded",
        )
    )
