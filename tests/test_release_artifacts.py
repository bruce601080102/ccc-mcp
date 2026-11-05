import io
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path


def _artifacts(dist: Path, *, wheel_version: str = "0.3.0", sdist_version: str = "0.3.0"):
    dist.mkdir()
    wheel = dist / f"ccc_mcp-{wheel_version}-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr(
            f"ccc_mcp-{wheel_version}.dist-info/METADATA",
            f"Metadata-Version: 2.3\nName: ccc-mcp\nVersion: {wheel_version}\n",
        )
    sdist = dist / f"ccc_mcp-{sdist_version}.tar.gz"
    contents = f"Metadata-Version: 2.3\nName: ccc-mcp\nVersion: {sdist_version}\n".encode()
    with tarfile.open(sdist, "w:gz") as archive:
        member = tarfile.TarInfo(f"ccc_mcp-{sdist_version}/PKG-INFO")
        member.size = len(contents)
        archive.addfile(member, io.BytesIO(contents))
    return wheel, sdist


def _check_release(tmp_path):
    script = Path(__file__).resolve().parents[1] / "scripts" / "release_artifacts.py"
    return subprocess.run(
        [sys.executable, str(script)], cwd=tmp_path, capture_output=True, text=True, check=False
    )


def test_release_version_is_read_from_matching_built_artifacts(tmp_path):
    _artifacts(tmp_path / "dist")
    result = _check_release(tmp_path)
    assert result.returncode == 0, result.stderr
    assert result.stdout == "version=0.3.0\n"


def test_release_version_rejects_mismatched_artifacts(tmp_path):
    _artifacts(tmp_path / "dist", sdist_version="0.2.0")
    result = _check_release(tmp_path)
    assert result.returncode != 0
    assert "Wheel version 0.3.0 != sdist version 0.2.0" in result.stderr


def test_release_version_rejects_missing_distribution(tmp_path):
    _, sdist = _artifacts(tmp_path / "dist")
    sdist.unlink()
    result = _check_release(tmp_path)
    assert result.returncode != 0
    assert "exactly one wheel and one sdist" in result.stderr


def test_release_version_rejects_filename_metadata_mismatch(tmp_path):
    wheel, _ = _artifacts(tmp_path / "dist")
    wheel.rename(wheel.with_name("ccc_mcp-0.4.0-py3-none-any.whl"))
    result = _check_release(tmp_path)
    assert result.returncode != 0
    assert "filenames and metadata have different versions" in result.stderr
