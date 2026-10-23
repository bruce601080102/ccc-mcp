import hashlib
import io
import json
import os
import zipfile

import pytest

from ccc_mcp.adapters import tunnel_client
from ccc_mcp.infra.errors import BridgeError


def make_zip(entry_name, content):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        zf.writestr(entry_name, content)
    return buffer.getvalue()


class FakeResponse:
    def __init__(self, data):
        self._data = data

    def read(self):
        return self._data

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def fake_urlopen_factory(
    archive_bytes, checksums_text, archive_name="tunnel-client-v1.0.0-windows-amd64.zip"
):
    release = {
        "assets": [
            {
                "name": archive_name,
                "browser_download_url": "https://example.invalid/" + archive_name,
            },
            {
                "name": "SHA256SUMS.txt",
                "browser_download_url": "https://example.invalid/SHA256SUMS.txt",
            },
        ]
    }

    def fake_urlopen(url, timeout=None):
        target = url if isinstance(url, str) else url.full_url
        if target.endswith("releases/latest"):
            return FakeResponse(json.dumps(release).encode("utf-8"))
        if target.endswith(archive_name):
            return FakeResponse(archive_bytes)
        if target.endswith("SHA256SUMS.txt"):
            return FakeResponse(checksums_text.encode("utf-8"))
        raise AssertionError(f"Unexpected URL: {target}")

    return fake_urlopen


@pytest.fixture(autouse=True)
def windows_amd64(monkeypatch):
    monkeypatch.setattr(tunnel_client.platform, "machine", lambda: "AMD64")
    monkeypatch.setattr(tunnel_client.platform, "system", lambda: "Windows")


def test_ensure_tunnel_client_downloads_and_verifies(tmp_path, monkeypatch):
    archive_bytes = make_zip("tunnel-client.exe", b"fake binary contents")
    digest = hashlib.sha256(archive_bytes).hexdigest()
    checksums = f"{digest}  tunnel-client-v1.0.0-windows-amd64.zip\n"
    monkeypatch.setattr(
        tunnel_client.urllib.request, "urlopen", fake_urlopen_factory(archive_bytes, checksums)
    )

    target = tunnel_client.ensure_tunnel_client(tmp_path)

    assert target == tmp_path / "tools" / "tunnel-client" / "tunnel-client.exe"
    assert target.read_bytes() == b"fake binary contents"
    assert json.loads(tunnel_client.tunnel_client_metadata_path(tmp_path).read_text()) == {
        "archive_name": "tunnel-client-v1.0.0-windows-amd64.zip",
        "platform_tag": "windows-amd64",
    }


def test_ensure_tunnel_client_rejects_sha256_mismatch(tmp_path, monkeypatch):
    archive_bytes = make_zip("tunnel-client.exe", b"fake binary contents")
    checksums = "0" * 64 + "  tunnel-client-v1.0.0-windows-amd64.zip\n"
    monkeypatch.setattr(
        tunnel_client.urllib.request, "urlopen", fake_urlopen_factory(archive_bytes, checksums)
    )

    with pytest.raises(BridgeError, match="SHA256 verification failed"):
        tunnel_client.ensure_tunnel_client(tmp_path)

    assert not (tmp_path / "tools" / "tunnel-client" / "tunnel-client.exe").exists()


def test_ensure_tunnel_client_skips_network_when_already_installed(tmp_path, monkeypatch):
    target = tmp_path / "tools" / "tunnel-client" / "tunnel-client.exe"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"already installed")
    tunnel_client.tunnel_client_metadata_path(tmp_path).write_text(
        json.dumps({"platform_tag": "windows-amd64"}), encoding="utf-8"
    )

    def fail_urlopen(*args, **kwargs):
        raise AssertionError("urlopen should not be called when already installed")

    monkeypatch.setattr(tunnel_client.urllib.request, "urlopen", fail_urlopen)

    result = tunnel_client.ensure_tunnel_client(tmp_path)

    assert result == target
    assert result.read_bytes() == b"already installed"


def test_ensure_tunnel_client_replaces_unstamped_legacy_binary(tmp_path, monkeypatch):
    target = tmp_path / "tools" / "tunnel-client" / "tunnel-client.exe"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"legacy binary")
    archive_bytes = make_zip("tunnel-client.exe", b"replacement binary")
    digest = hashlib.sha256(archive_bytes).hexdigest()
    checksums = f"{digest}  tunnel-client-v1.0.0-windows-amd64.zip\n"
    monkeypatch.setattr(
        tunnel_client.urllib.request, "urlopen", fake_urlopen_factory(archive_bytes, checksums)
    )

    assert tunnel_client.ensure_tunnel_client(tmp_path).read_bytes() == b"replacement binary"


def test_ensure_tunnel_client_replaces_wrong_architecture_for_same_binary_name(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(tunnel_client.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(tunnel_client.platform, "machine", lambda: "arm64")
    target = tmp_path / "tools" / "tunnel-client" / "tunnel-client"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"x64 binary")
    tunnel_client.tunnel_client_metadata_path(tmp_path).write_text(
        json.dumps({"platform_tag": "darwin-amd64"}), encoding="utf-8"
    )
    archive_bytes = make_zip("tunnel-client", b"arm64 binary")
    digest = hashlib.sha256(archive_bytes).hexdigest()
    archive_name = "tunnel-client-v1.0.0-darwin-arm64.zip"
    checksums = f"{digest}  {archive_name}\n"
    monkeypatch.setattr(
        tunnel_client.urllib.request,
        "urlopen",
        fake_urlopen_factory(archive_bytes, checksums, archive_name=archive_name),
    )

    assert tunnel_client.ensure_tunnel_client(tmp_path).read_bytes() == b"arm64 binary"
    assert json.loads(tunnel_client.tunnel_client_metadata_path(tmp_path).read_text()) == {
        "archive_name": archive_name,
        "platform_tag": "darwin-arm64",
    }


@pytest.mark.skipif(os.name == "nt", reason="POSIX executable mode bits")
def test_ensure_tunnel_client_downloads_darwin_arm64_and_sets_executable_bit(tmp_path, monkeypatch):
    monkeypatch.setattr(tunnel_client.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(tunnel_client.platform, "machine", lambda: "arm64")
    archive_bytes = make_zip("tunnel-client", b"fake macos binary")
    digest = hashlib.sha256(archive_bytes).hexdigest()
    archive_name = "tunnel-client-v1.0.0-darwin-arm64.zip"
    checksums = f"{digest}  {archive_name}\n"
    monkeypatch.setattr(
        tunnel_client.urllib.request,
        "urlopen",
        fake_urlopen_factory(archive_bytes, checksums, archive_name=archive_name),
    )

    target = tunnel_client.ensure_tunnel_client(tmp_path)

    assert target == tmp_path / "tools" / "tunnel-client" / "tunnel-client"
    assert target.read_bytes() == b"fake macos binary"
    assert target.stat().st_mode & 0o111


@pytest.mark.skipif(os.name == "nt", reason="POSIX executable mode bits")
def test_ensure_tunnel_client_downloads_linux_amd64_and_sets_executable_bit(tmp_path, monkeypatch):
    monkeypatch.setattr(tunnel_client.platform, "system", lambda: "Linux")
    monkeypatch.setattr(tunnel_client.platform, "machine", lambda: "x86_64")
    archive_bytes = make_zip("tunnel-client", b"fake linux binary")
    digest = hashlib.sha256(archive_bytes).hexdigest()
    archive_name = "tunnel-client-v1.0.0-linux-amd64.zip"
    checksums = f"{digest}  {archive_name}\n"
    monkeypatch.setattr(
        tunnel_client.urllib.request,
        "urlopen",
        fake_urlopen_factory(archive_bytes, checksums, archive_name=archive_name),
    )

    target = tunnel_client.ensure_tunnel_client(tmp_path)

    assert target == tmp_path / "tools" / "tunnel-client" / "tunnel-client"
    assert target.read_bytes() == b"fake linux binary"
    assert target.stat().st_mode & 0o111
    assert json.loads(tunnel_client.tunnel_client_metadata_path(tmp_path).read_text()) == {
        "archive_name": archive_name,
        "platform_tag": "linux-amd64",
    }


def test_ensure_tunnel_client_rejects_unsupported_architecture(tmp_path, monkeypatch):
    monkeypatch.setattr(tunnel_client.platform, "system", lambda: "Linux")
    monkeypatch.setattr(tunnel_client.platform, "machine", lambda: "ppc64le")

    with pytest.raises(BridgeError, match="Unsupported CPU architecture"):
        tunnel_client.ensure_tunnel_client(tmp_path)


def test_ensure_tunnel_client_rejects_unsupported_os(tmp_path, monkeypatch):
    monkeypatch.setattr(tunnel_client.platform, "system", lambda: "SunOS")

    with pytest.raises(BridgeError, match="Unsupported operating system"):
        tunnel_client.ensure_tunnel_client(tmp_path)
