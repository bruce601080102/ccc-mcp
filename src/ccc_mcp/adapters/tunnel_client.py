"""Download, verify and install the official OpenAI tunnel-client binary.

Ports scripts/install-tunnel-client.sh so `ccc-mcp connect` can self-install
without requiring Git Bash, curl, unzip or sha256sum.
"""

import hashlib
import json
import platform
import re
import tempfile
import urllib.request
import zipfile
from pathlib import Path

from ..infra.config import setting_env
from ..infra.errors import BridgeError

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


def ensure_tunnel_client(home: Path, force: bool = False) -> Path:
    """Return a verified tunnel-client binary under `home`, downloading it if needed."""
    binary_name = _binary_name()
    target = tunnel_client_path(home)
    if target.is_file() and not force:
        return target

    platform_tag = _platform_tag()
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
            zf.extractall(tmp)
        extracted = next(Path(tmp).rglob(binary_name), None)
        if extracted is None:
            raise BridgeError(f"Downloaded archive does not contain {binary_name}")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(extracted.read_bytes())
        if platform.system().lower() != "windows":
            # write_bytes above does not carry over the executable bit from the zip.
            target.chmod(target.stat().st_mode | 0o111)
    return target
