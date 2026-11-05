import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "release_version.py"


def _run(*args):
    return subprocess.run(
        [sys.executable, str(SCRIPT), *map(str, args)],
        capture_output=True,
        text=True,
        check=False,
    )


def test_manual_release_version_updates_only_the_requested_checkout(tmp_path):
    version_file = tmp_path / "__init__.py"
    version_file.write_text('"""package"""\n\n__version__ = "0.2.0"\n')

    result = _run("0.4.0rc1", "--path", version_file)

    assert result.returncode == 0, result.stderr
    assert version_file.read_text() == '"""package"""\n\n__version__ = "0.4.0rc1"\n'


def test_manual_release_version_rejects_noncanonical_or_private_versions(tmp_path):
    version_file = tmp_path / "__init__.py"
    version_file.write_text('__version__ = "0.2.0"\n')

    for version in ("v0.3.0", "0.3.0+private", "0.3.0\nunsafe"):
        result = _run(version, "--path", version_file)
        assert result.returncode != 0
        assert version_file.read_text() == '__version__ = "0.2.0"\n'


def test_manual_release_version_validation_does_not_write(tmp_path):
    version_file = tmp_path / "__init__.py"
    version_file.write_text('__version__ = "0.2.0"\n')

    result = _run("0.3.0", "--validate-only", "--path", version_file)

    assert result.returncode == 0, result.stderr
    assert version_file.read_text() == '__version__ = "0.2.0"\n'
