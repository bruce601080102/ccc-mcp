"""Read optional account usage through Codex's documented local App Server.

The Codex process owns authentication. This module never reads credentials or
stores account data; the web console receives only the documented metrics.
"""

import asyncio
import json
import shutil


def _nonnegative_int(value):
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def _usage_metrics(result):
    summary = result.get("summary") if isinstance(result, dict) else None
    summary = summary if isinstance(summary, dict) else {}
    fields = (
        "lifetimeTokens",
        "peakDailyTokens",
        "longestRunningTurnSec",
        "currentStreakDays",
        "longestStreakDays",
    )
    clean_summary = {key: _nonnegative_int(summary.get(key)) for key in fields}
    daily = []
    buckets = result.get("dailyUsageBuckets") if isinstance(result, dict) else None
    if isinstance(buckets, list):
        for bucket in buckets:
            if not isinstance(bucket, dict):
                continue
            day, tokens = bucket.get("startDate"), _nonnegative_int(bucket.get("tokens"))
            if isinstance(day, str) and len(day) == 10 and tokens is not None:
                daily.append({"date": day, "tokens": tokens})
    return {"summary": clean_summary, "daily": sorted(daily, key=lambda row: row["date"])}


def _rate_metrics(result):
    if not isinstance(result, dict):
        return []
    buckets = result.get("rateLimitsByLimitId")
    if not isinstance(buckets, dict):
        single = result.get("rateLimits")
        buckets = {single.get("limitId", "default"): single} if isinstance(single, dict) else {}
    windows = []
    for bucket in buckets.values():
        if not isinstance(bucket, dict):
            continue
        name = bucket.get("limitName") or bucket.get("limitId") or "Usage"
        for key in ("primary", "secondary"):
            window = bucket.get(key)
            if not isinstance(window, dict):
                continue
            used = window.get("usedPercent")
            duration = _nonnegative_int(window.get("windowDurationMins"))
            reset = _nonnegative_int(window.get("resetsAt"))
            if not isinstance(used, (int, float)) or isinstance(used, bool):
                continue
            windows.append(
                {
                    "name": str(name),
                    "used_percent": max(0, min(100, used)),
                    "duration_mins": duration,
                    "resets_at": reset,
                }
            )
    return windows


async def read_account_usage(timeout=10):
    """Return account-wide token activity and plan windows, or a safe fallback."""
    executable = shutil.which("codex")
    if not executable:
        return {"available": False, "reason": "codex_unavailable"}
    try:
        process = await asyncio.create_subprocess_exec(
            executable,
            "app-server",
            "--listen",
            "stdio://",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
    except OSError:
        return {"available": False, "reason": "app_server_unavailable"}

    async def request(message):
        process.stdin.write((json.dumps(message) + "\n").encode())
        await process.stdin.drain()
        for _ in range(100):
            line = await process.stdout.readline()
            if not line:
                raise RuntimeError("App Server closed")
            try:
                response = json.loads(line)
            except ValueError:
                continue
            if isinstance(response, dict) and response.get("id") == message["id"]:
                return response.get("result") if isinstance(response.get("result"), dict) else None
        raise RuntimeError("App Server did not respond")

    try:
        async with asyncio.timeout(timeout):
            initialized = await request(
                {
                    "method": "initialize",
                    "id": 1,
                    "params": {
                        "clientInfo": {"name": "ccc_mcp", "title": "ccc-mcp", "version": "0.1.0"}
                    },
                }
            )
            if initialized is None:
                return {"available": False, "reason": "app_server_unavailable"}
            process.stdin.write(b'{"method":"initialized","params":{}}\n')
            await process.stdin.drain()
            usage = await request({"method": "account/usage/read", "id": 2})
            limits = await request({"method": "account/rateLimits/read", "id": 3})
            if usage is None and limits is None:
                return {"available": False, "reason": "account_usage_unavailable"}
            return {
                "available": True,
                "tokens_available": usage is not None,
                "limits_available": limits is not None,
                **_usage_metrics(usage),
                "rate_limits": _rate_metrics(limits),
            }
    except (TimeoutError, OSError, RuntimeError, BrokenPipeError):
        return {"available": False, "reason": "account_usage_unavailable"}
    finally:
        if process.returncode is None:
            try:
                process.kill()
            except ProcessLookupError:
                pass
        # Drain stdout as well: asyncio may otherwise wait indefinitely for a
        # killed App Server whose pipe still has buffered notifications.
        try:
            await asyncio.wait_for(process.communicate(), timeout=1)
        except TimeoutError:
            pass
