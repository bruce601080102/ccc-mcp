import io
import zipfile

import pytest

from ccc_mcp.adapters import skills
from ccc_mcp.infra.errors import BridgeError


class FakeResponse:
    def __init__(self, data):
        self._data = data

    def read(self):
        return self._data

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def make_zip(files):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        for name, content in files.items():
            zf.writestr(name, content)
    return buffer.getvalue()


def test_install_from_local_path_with_frontmatter(tmp_path):
    source = tmp_path / "source-dir"
    source.mkdir()
    (source / "SKILL.md").write_text(
        "---\nname: my-skill\ndescription: Use for X.\n---\nBody text.\n", encoding="utf-8"
    )

    result = skills.install_skill(tmp_path / "home", str(source))

    assert result["id"] == "my-skill"
    assert result["description"] == "Use for X."
    installed = tmp_path / "home" / "skills" / "my-skill" / "SKILL.md"
    assert installed.is_file()


def test_install_from_local_path_without_frontmatter_uses_dirname(tmp_path):
    source = tmp_path / "plain-skill"
    source.mkdir()
    (source / "SKILL.md").write_text("Just a body, no frontmatter.\n", encoding="utf-8")

    result = skills.install_skill(tmp_path / "home", str(source))

    assert result["id"] == "plain-skill"
    assert result["description"] == ""


def test_install_from_local_path_copies_extra_files(tmp_path):
    source = tmp_path / "with-script"
    source.mkdir()
    (source / "SKILL.md").write_text("---\nname: with-script\n---\nBody.\n", encoding="utf-8")
    (source / "helper.py").write_text("print('hi')\n", encoding="utf-8")

    skills.install_skill(tmp_path / "home", str(source))

    helper = tmp_path / "home" / "skills" / "with-script" / "helper.py"
    assert helper.read_text(encoding="utf-8") == "print('hi')\n"


def test_install_requires_skill_md(tmp_path):
    source = tmp_path / "empty-dir"
    source.mkdir()
    with pytest.raises(BridgeError, match="SKILL.md"):
        skills.install_skill(tmp_path / "home", str(source))


def test_install_rejects_nonexistent_path(tmp_path):
    with pytest.raises(BridgeError, match="not a directory"):
        skills.install_skill(tmp_path / "home", str(tmp_path / "missing"))


def test_install_from_url_single_md_file(tmp_path, monkeypatch):
    content = b"---\nname: url-skill\ndescription: From a URL.\n---\nBody.\n"
    monkeypatch.setattr(skills.urllib.request, "urlopen", lambda url, timeout=None: FakeResponse(content))

    result = skills.install_skill(tmp_path / "home", "https://example.invalid/url-skill.md")

    assert result["id"] == "url-skill"
    assert result["description"] == "From a URL."


def test_install_from_url_zip_archive(tmp_path, monkeypatch):
    archive = make_zip(
        {
            "my-pack/SKILL.md": "---\nname: zip-skill\ndescription: From a zip.\n---\nBody.\n",
            "my-pack/helper.sh": "echo hi\n",
        }
    )
    monkeypatch.setattr(skills.urllib.request, "urlopen", lambda url, timeout=None: FakeResponse(archive))

    result = skills.install_skill(tmp_path / "home", "https://example.invalid/pack.zip")

    assert result["id"] == "zip-skill"
    helper = tmp_path / "home" / "skills" / "zip-skill" / "helper.sh"
    assert helper.read_text(encoding="utf-8") == "echo hi\n"


def test_install_from_url_zip_rejects_path_traversal(tmp_path, monkeypatch):
    archive = make_zip(
        {
            "pack/SKILL.md": "---\nname: evil\n---\nBody.\n",
            "../../evil.txt": "pwned",
        }
    )
    monkeypatch.setattr(skills.urllib.request, "urlopen", lambda url, timeout=None: FakeResponse(archive))

    with pytest.raises(BridgeError):
        skills.install_skill(tmp_path / "home", "https://example.invalid/evil.zip")

    assert not (tmp_path / "evil.txt").exists()


def test_install_from_url_unsupported_extension(tmp_path):
    with pytest.raises(BridgeError, match="must end in"):
        skills.install_skill(tmp_path / "home", "https://example.invalid/skill.tar.gz")


def test_list_skills_empty_when_none_installed(tmp_path):
    assert skills.list_skills(tmp_path / "home") == []


def test_remove_skill_not_installed_raises(tmp_path):
    with pytest.raises(BridgeError, match="not installed"):
        skills.remove_skill(tmp_path / "home", "missing")


def test_remove_skill_deletes_directory(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "SKILL.md").write_text("---\nname: temp\n---\nBody.\n", encoding="utf-8")
    skills.install_skill(tmp_path / "home", str(source))

    result = skills.remove_skill(tmp_path / "home", "temp")

    assert result == {"id": "temp", "removed": True}
    assert not (tmp_path / "home" / "skills" / "temp").exists()


def test_available_skills_includes_full_content(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "SKILL.md").write_text(
        "---\nname: full\ndescription: d\n---\nFull body text.\n", encoding="utf-8"
    )
    skills.install_skill(tmp_path / "home", str(source))

    result = skills.available_skills(tmp_path / "home")

    assert result == [
        {
            "id": "full",
            "name": "full",
            "description": "d",
            "path": str(tmp_path / "home" / "skills" / "full"),
            "content": "Full body text.",
        }
    ]
