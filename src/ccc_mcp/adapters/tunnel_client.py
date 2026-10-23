"""Download, verify and install the official OpenAI tunnel-client binary.

Ports scripts/install-tunnel-client.sh so `ccc-mcp connect` can self-install
without requiring Git Bash, curl, unzip or sha256sum.
"""

import hashlib
import json
import os
import platform
import re
import tempfile
import urllib.request
import zipfile
from pathlib import Path

from ..infra.config import setting_env
from ..infra.errors import BridgeError
from ..infra.security import atomic_write

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
