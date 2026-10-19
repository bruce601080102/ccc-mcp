import hashlib
import json
from contextlib import contextmanager
from contextvars import ContextVar

from ..adapters.storage import now

ACTIVE_AUDIT = ContextVar("ccc_mcp_audit", default=None)


@contextmanager
def audit_scope(audit):
    token = ACTIVE_AUDIT.set(audit)
    try:
        yield
    finally:
        ACTIVE_AUDIT.reset(token)


def usage_from_output(provider, stdout):
    """Only parse native CLI metadata, never model-authored AgentReport claims."""
    result = {"available": False, "source": "cli_output", "reported_models": []}
    if provider == "codex":
        usages = []
        for line in stdout.splitlines():
            try:
                item = json.loads(line)
            except ValueError:
                continue
            if (
                isinstance(item, dict)
                and item.get("type") == "turn.completed"
                and isinstance(item.get("usage"), dict)
            ):
                usages.append(item["usage"])
        tokens = {}
        for key in (
            "input_tokens",
            "cached_input_tokens",
            "output_tokens",
            "reasoning_output_tokens",
        ):
            values = [
                u[key]
                for u in usages
                if isinstance(u.get(key), int) and not isinstance(u[key], bool) and u[key] >= 0
            ]
            if values:
                tokens[key] = sum(values)
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
            tokens = {
                key: value
                for key, value in usage.items()
                if key
                in {
                    "input_tokens",
                    "output_tokens",
                    "cache_read_input_tokens",
                    "cache_creation_input_tokens",
                }
                and isinstance(value, int)
                and not isinstance(value, bool)
                and value >= 0
            }
            if tokens:
                result.update(available=True, tokens=tokens)
        models = envelope.get("modelUsage", {})
        if isinstance(models, dict):
            result["reported_models"] = list(models)
        cost = envelope.get("total_cost_usd")
        if isinstance(cost, (int, float)) and not isinstance(cost, bool) and cost >= 0:
            result["cli_estimated_cost_usd"] = cost
            result["cost_note"] = "CLI estimate; not a subscription invoice or remaining quota"
    return result


def record_output(provider, stdout, stderr=""):
    audit = ACTIVE_AUDIT.get()
    if audit is not None:
        audit["usage"] = usage_from_output(provider, stdout)
        audit["stdout_sha256"] = hashlib.sha256(stdout.encode()).hexdigest()
        audit["stderr_sha256"] = hashlib.sha256(stderr.encode()).hexdigest()


def process_started(argv, cwd, pid):
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
            "rate_limit_exceeded",
        )
    )
