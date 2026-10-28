"""Download, verify and install the official OpenAI tunnel-client binary.

Ports scripts/install-tunnel-client.sh so `ccc-mcp connect` can self-install
without requiring Git Bash, curl, unzip or sha256sum.
"""

import hashlib
import json
import os
import platform
import re
import secrets
import shlex
import subprocess
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path

from ..infra.config import setting_env
from ..infra.errors import BridgeError
from ..infra.security import atomic_write
from .storage import now

DEFAULT_RELEASE_API = "https://api.github.com/repos/openai/tunnel-client/releases/latest"
OS_TAGS = {"windows": "windows", "darwin": "darwin", "linux": "linux"}


def _platform_tag():
    system = platform.system().lower()
    os_tag = OS_TAGS.get(system)
    if os_tag is None:
        raise BridgeError(f"Unsupported operating system: {platform.system()}")
    machine = platform.machine().lower()
    if machine in {"x86_64", "amd64"}:
        arch_tag = "amd64"
    elif machine in {"arm64", "aarch64"}:
        arch_tag = "arm64"
    else:
        raise BridgeError(f"Unsupported CPU architecture: {platform.machine()}")
    return f"{os_tag}-{arch_tag}"


def _binary_name():
    return "tunnel-client.exe" if platform.system().lower() == "windows" else "tunnel-client"


def _safe_extractall(zf, destination):
    """Reject zip-slip members (absolute paths or `..` segments) before writing anything.

    The archive is already SHA256-verified against the official release's checksums, but
    extractall() by itself does not validate member paths, so a crafted entry could still
    write outside `destination` (e.g. `../../.ssh/authorized_keys`). A well-formed archive
    extracts exactly as before; this only narrows what a malformed one can do.
    """
    destination = Path(destination).resolve()
    for member in zf.namelist():
        target = (destination / member).resolve()
        if target != destination and destination not in target.parents:
            raise BridgeError(f"Archive entry escapes extraction directory: {member}")
    zf.extractall(destination)


def _fetch_json(url):
    request = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def _download(url):
    with urllib.request.urlopen(url, timeout=120) as response:
        return response.read()


def _expected_sha256(checksums_text, archive_name):
    for line in checksums_text.splitlines():
        parts = line.split()
        if len(parts) != 2:
            continue
        digest, name = parts
        if name.lstrip("*") == archive_name:
            return digest.lower()
    raise BridgeError(f"SHA256SUMS.txt does not list {archive_name}")


def tunnel_client_path(home: Path) -> Path:
    """Where ensure_tunnel_client installs/looks for the verified binary, without
    triggering a download — used by `doctor` to report whether it's already there."""
    return Path(home) / "tools" / "tunnel-client" / _binary_name()


def tunnel_client_metadata_path(home: Path) -> Path:
    """Architecture stamp for the installed binary.

    The binary's filename is deliberately the same for x64 and arm64 on macOS/Linux.
    A host can therefore keep an incompatible executable after a Rosetta/native Python
    switch unless we retain the platform tag that produced it.
    """
    return Path(home) / "tools" / "tunnel-client" / "installation.json"


def _installed_platform_tag(home: Path) -> str | None:
    try:
        record = json.loads(tunnel_client_metadata_path(home).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    value = record.get("platform_tag") if isinstance(record, dict) else None
    return value if isinstance(value, str) else None


def _install_binary(source: Path, target: Path) -> None:
    """Atomically replace the client, preserving a working old binary on failure.

    Windows cannot replace a running executable. Surface that as an actionable error
    rather than truncating the existing client with a direct write.
    """
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, staged_name = tempfile.mkstemp(prefix=".tunnel-client-", dir=target.parent)
    staged = Path(staged_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(source.read_bytes())
            handle.flush()
            os.fsync(handle.fileno())
        if platform.system().lower() != "windows":
            staged.chmod(staged.stat().st_mode | 0o111)
        try:
            os.replace(staged, target)
        except PermissionError as exc:
            raise BridgeError(
                "Cannot replace tunnel-client while it is running; stop ccc-mcp connect and retry"
            ) from exc
    finally:
        if staged.exists():
            staged.unlink()


def ensure_tunnel_client(home: Path, force: bool = False) -> Path:
    """Return a verified client for this OS/CPU, downloading it when stale or absent."""
    binary_name = _binary_name()
    target = tunnel_client_path(home)
    platform_tag = _platform_tag()
    if target.is_file() and not force and _installed_platform_tag(home) == platform_tag:
        return target

    release_api = setting_env("TUNNEL_CLIENT_RELEASE_API", DEFAULT_RELEASE_API)
    release = _fetch_json(release_api)
    assets = {asset["name"]: asset["browser_download_url"] for asset in release.get("assets", [])}
    pattern = re.compile(rf"^tunnel-client-v[^/]+-{re.escape(platform_tag)}\.zip$")
    matches = [name for name in assets if pattern.match(name)]
    if len(matches) != 1 or "SHA256SUMS.txt" not in assets:
        raise BridgeError(
            f"Official tunnel-client release does not contain the expected {platform_tag} archive/checksum"
        )
    archive_name = matches[0]

    archive_bytes = _download(assets[archive_name])
    checksums_text = _download(assets["SHA256SUMS.txt"]).decode("utf-8")
    expected = _expected_sha256(checksums_text, archive_name)
    actual = hashlib.sha256(archive_bytes).hexdigest()
    if actual != expected:
        raise BridgeError(f"SHA256 verification failed for {archive_name}; nothing was installed")

    with tempfile.TemporaryDirectory() as tmp:
        archive_path = Path(tmp) / archive_name
        archive_path.write_bytes(archive_bytes)
        with zipfile.ZipFile(archive_path) as zf:
            _safe_extractall(zf, tmp)
        extracted = next(Path(tmp).rglob(binary_name), None)
        if extracted is None:
            raise BridgeError(f"Downloaded archive does not contain {binary_name}")
        _install_binary(extracted, target)
        atomic_write(
            tunnel_client_metadata_path(home),
            json.dumps(
                {"platform_tag": platform_tag, "archive_name": archive_name},
                ensure_ascii=False,
                sort_keys=True,
            )
            + "\n",
        )
    return target


def profile_mcp_interpreter(profile_path: Path) -> str | None:
    """Pull the python executable out of a generated profile's mcp_command, or None."""
    match = re.search(r'command:\s*"((?:[^"\\]|\\.)*)"', profile_path.read_text(encoding="utf-8"))
    if not match:
        return None
    unescaped = match.group(1).replace('\\"', '"').replace("\\\\", "\\")
    try:
        parts = shlex.split(unescaped)
    except ValueError:
        return None
    return parts[0] if parts else None


def profile_is_stale(profile_path: Path) -> bool:
    """True if the profile's mcp_command no longer points to a usable ccc_mcp install.

    This happens when `connect` was first run from a different venv/conda env than the
    one that has ccc_mcp installed: the saved command keeps that old interpreter forever,
    since the profile is only (re)written when it does not already exist.
    """
    interpreter = profile_mcp_interpreter(profile_path)
    if not interpreter:
        return True
    try:
        result = subprocess.run(
            [interpreter, "-c", "import ccc_mcp"],
            capture_output=True,
            stdin=subprocess.DEVNULL,
            timeout=15,
            shell=False,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return True
    return result.returncode != 0


# --- Runtime key storage -------------------------------------------------------
#
# Remembering the OpenAI Platform runtime Secret Key is an explicit, opt-in
# convenience (CLI `--remember`, the web console's "Remember on this device"):
# written to <home>/tunnel/credentials.json, chmod 0600, never logged, never
# echoed back in full once saved. This is the same trust model as every other
# local credential this project already relies on implicitly (git, the native
# Codex/Claude CLI's own saved logins) — the OS account is the boundary, not an
# extra passphrase — and the same pattern tools like `gh auth login` or AWS's
# `~/.aws/credentials` use. It is never sent anywhere except as the
# CONTROL_PLANE_API_KEY environment variable of the tunnel-client subprocess.


def credentials_path(home: Path) -> Path:
    return Path(home) / "tunnel" / "credentials.json"


def _read_credentials(home: Path) -> dict:
    try:
        data = json.loads(credentials_path(home).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def save_tunnel_key(home: Path, tunnel_id: str, api_key: str) -> None:
    path = credentials_path(home)
    records = _read_credentials(home)
    records[tunnel_id] = {"api_key": api_key, "saved_at": now()}
    atomic_write(path, json.dumps(records, ensure_ascii=False, sort_keys=True) + "\n")
    try:
        path.chmod(0o600)
    except OSError:
        pass


def load_tunnel_key(home: Path, tunnel_id: str) -> str | None:
    record = _read_credentials(home).get(tunnel_id)
    return record.get("api_key") if isinstance(record, dict) else None


def forget_tunnel_key(home: Path, tunnel_id: str) -> bool:
    path = credentials_path(home)
    records = _read_credentials(home)
    if records.pop(tunnel_id, None) is None:
        return False
    atomic_write(path, json.dumps(records, ensure_ascii=False, sort_keys=True) + "\n")
    return True


def remembered_tunnels(home: Path) -> list[str]:
    return sorted(key for key in _read_credentials(home) if key.startswith("tunnel_"))


def connection_profiles(home: Path) -> list[dict]:
    """Public metadata only; secret values must never leave the host API."""
    result = []
    for profile_id, record in _read_credentials(home).items():
        if not isinstance(record, dict) or not isinstance(record.get("api_key"), str):
            continue
        tunnel_id = record.get("tunnel_id", profile_id)
        if not isinstance(tunnel_id, str) or not re.fullmatch(r"tunnel_[a-z0-9]{32}", tunnel_id):
            continue
        name = record.get("name")
        result.append(
            {
                "id": profile_id,
                "name": name if isinstance(name, str) and name else tunnel_id,
                "tunnel_id": tunnel_id,
            }
        )
    return sorted(result, key=lambda row: row["name"].casefold())


def get_connection_profile(home: Path, profile_id: str) -> dict | None:
    record = _read_credentials(home).get(profile_id)
    if not isinstance(record, dict) or not isinstance(record.get("api_key"), str):
        return None
    tunnel_id = record.get("tunnel_id", profile_id)
    if not isinstance(tunnel_id, str) or not re.fullmatch(r"tunnel_[a-z0-9]{32}", tunnel_id):
        return None
    name = record.get("name")
    return {
        "id": profile_id,
        "name": name if isinstance(name, str) and name else tunnel_id,
        "tunnel_id": tunnel_id,
        "api_key": record["api_key"],
    }


def save_connection_profile(
    home: Path, tunnel_id: str, api_key: str, name: str | None, profile_id: str | None = None
) -> dict:
    validate_tunnel_id(tunnel_id)
    validate_api_key(api_key)
    if not isinstance(name, str) or not name.strip() or len(name.strip()) > 60:
        raise BridgeError("Connection name must be 1–60 characters")
    name = name.strip()
    records = _read_credentials(home)
    if profile_id and profile_id not in records:
        raise BridgeError("Saved connection not found")
    if any(
        (record.get("name") if isinstance(record.get("name"), str) else key).casefold()
        == name.casefold()
        for key, record in records.items()
        if key != profile_id and isinstance(record, dict)
    ):
        raise BridgeError("A connection with this name already exists")
    profile_id = profile_id or "conn_" + secrets.token_hex(8)
    records[profile_id] = {
        "name": name,
        "tunnel_id": tunnel_id,
        "api_key": api_key,
        "saved_at": now(),
    }
    path = credentials_path(home)
    atomic_write(path, json.dumps(records, ensure_ascii=False, sort_keys=True) + "\n")
    try:
        path.chmod(0o600)
    except OSError:
        pass
    return {"id": profile_id, "name": name, "tunnel_id": tunnel_id}


def forget_connection_profile(home: Path, profile_id: str) -> bool:
    if not isinstance(profile_id, str) or not profile_id:
        return False
    path = credentials_path(home)
    records = _read_credentials(home)
    if records.pop(profile_id, None) is None:
        return False
    atomic_write(path, json.dumps(records, ensure_ascii=False, sort_keys=True) + "\n")
    try:
        path.chmod(0o600)
    except OSError:
        pass
    return True


# --- Shared connect preamble ----------------------------------------------------


def validate_tunnel_id(tunnel_id: str) -> None:
    if not re.fullmatch(r"tunnel_[a-z0-9]{32}", tunnel_id or ""):
        raise BridgeError("Usage: ccc-mcp connect tunnel_YOUR_ID")


def validate_api_key(api_key: str) -> None:
    if not api_key:
        raise BridgeError("No key entered")
    if any(c.isspace() for c in api_key) or api_key.startswith("tunnel_"):
        raise BridgeError(
            "Invalid key input: enter only the runtime Secret Key, "
            "without spaces/newlines or a tunnel ID"
        )


def provider_login_status() -> dict:
    """Structured install/login status for codex and claude — the same checks
    `connect` has always printed, without tying them to a terminal, so both the
    CLI and the web console can show the same information."""
    from .agents.base import native_executable

    statuses = {}
    for executable, status_args, login_hint in [
        ("codex", ["login", "status"], "codex login"),
        ("claude", ["auth", "status", "--text"], "claude auth login"),
    ]:
        try:
            path = native_executable(executable)
        except BridgeError:
            statuses[executable] = {
                "installed": False,
                "logged_in": False,
                "hint": f"{executable} not installed",
            }
            continue
        try:
            result = subprocess.run(
                [path, *status_args],
                capture_output=True,
                stdin=subprocess.DEVNULL,
                timeout=15,
                shell=False,
                check=False,
            )
            logged_in = result.returncode == 0
        except (OSError, subprocess.TimeoutExpired):
            logged_in = False
        statuses[executable] = {
            "installed": True,
            "logged_in": logged_in,
            "hint": None if logged_in else f"Finish login with: {login_hint}",
        }
    return statuses


def prepare_tunnel_connection(home: Path, tunnel_id: str, api_key: str) -> dict:
    """Shared preamble for `ccc-mcp connect` (CLI) and the web console's Connect
    page: verify tunnel-client is installed, confirm the runtime key actually has
    access to this tunnel, and create (or repair a stale) profile. Returns
    ``{"client", "profile", "profile_dir", "env"}`` ready for a `tunnel-client run`
    subprocess — it never starts that long-lived process itself, since the CLI and
    the web console supervise it differently (a blocking foreground subprocess vs.
    an asyncio-managed background one).
    """
    validate_tunnel_id(tunnel_id)
    validate_api_key(api_key)
    home = Path(home)
    client = ensure_tunnel_client(home)

    env = {k: v for k, v in os.environ.items() if k not in {"OPENAI_ADMIN_KEY", "OPENAI_API_KEY"}}
    env["CONTROL_PLANE_API_KEY"] = api_key
    check = subprocess.run(
        [str(client), "admin", "tunnels", "get", tunnel_id],
        env=env,
        shell=False,
        check=False,
        capture_output=True,
        stdin=subprocess.DEVNULL,
        timeout=30,
    )
    if check.returncode != 0:
        raise BridgeError(
            "Authentication/access check failed; connection was not started. Check the "
            "runtime key, its Platform organization, and Tunnels Read + Use permissions."
        )

    profile = f"ccc-mcp-{tunnel_id.removeprefix('tunnel_')}"
    profile_dir = home / "tunnel"
    profile_dir.mkdir(parents=True, exist_ok=True)
    # tunnel-client parses --mcp-command as a command line. Backslashes in
    # Windows paths are consumed as escapes, so use forward slashes here.
    mcp_command = (
        f'"{Path(sys.executable).absolute().as_posix()}" '
        f'-m ccc_mcp --home "{home.resolve().as_posix()}" serve'
    )
    profile_paths = (profile_dir / f"{profile}.yaml", profile_dir / f"{profile}.yml")
    existing_profile = next((path for path in profile_paths if path.is_file()), None)
    stale_profile_recreated = False
    if existing_profile is not None and profile_is_stale(existing_profile):
        existing_profile.unlink()
        stale_profile_recreated = True
    if not any(path.is_file() for path in profile_paths):
        initialized = subprocess.run(
            [
                str(client),
                "init",
                "--sample",
                "sample_mcp_stdio_local",
                "--profile",
                profile,
                "--profile-dir",
                str(profile_dir),
                "--tunnel-id",
                tunnel_id,
                "--mcp-command",
                mcp_command,
                "--control-plane-api-key-ref",
                "env:CONTROL_PLANE_API_KEY",
                "--health-listen-addr",
                "127.0.0.1:0",
            ],
            env={**os.environ, "CONTROL_PLANE_API_KEY": api_key},
            shell=False,
            check=False,
            capture_output=True,
            stdin=subprocess.DEVNULL,
        )
        if initialized.returncode != 0:
            raise BridgeError(
                "tunnel-client init failed; the profile was not created. "
                f"{initialized.stderr.decode('utf-8', 'replace').strip()[:500]}"
            )
        if not any(path.is_file() for path in profile_paths):
            raise BridgeError("tunnel-client init returned success but no profile file was created")

    diagnosed = subprocess.run(
        [
            str(client),
            "doctor",
            "--profile",
            profile,
            "--profile-dir",
            str(profile_dir),
            "--explain",
        ],
        env={**os.environ, "CONTROL_PLANE_API_KEY": api_key},
        shell=False,
        check=False,
        capture_output=True,
        stdin=subprocess.DEVNULL,
        timeout=60,
    )
    if diagnosed.returncode != 0:
        raise BridgeError("tunnel-client doctor failed; the connection was not started")

    return {
        "client": client,
        "profile": profile,
        "profile_dir": profile_dir,
        "env": {**os.environ, "CONTROL_PLANE_API_KEY": api_key},
        "stale_profile_recreated": stale_profile_recreated,
    }
