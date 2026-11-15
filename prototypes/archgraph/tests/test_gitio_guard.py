import pytest
from archgraph import gitio


@pytest.mark.parametrize(
    "verb",
    [
        "add",
        "commit",
        "reset",
        "checkout",
        "push",
        "update-index",
        "read-tree",
        "write-tree",
        "hash-object",
        "stash",
        "clean",
        "rm",
    ],
)
def test_write_capable_git_verbs_are_refused_before_any_process_starts(tmp_path, verb, monkeypatch):
    def fail(*_args, **_kwargs):
        raise AssertionError("a process must not start for a refused verb")

    monkeypatch.setattr(gitio.subprocess, "run", fail)
    with pytest.raises(gitio.GitError):
        gitio._git(tmp_path, verb)


def test_optional_locks_are_disabled_for_every_read(tmp_path, monkeypatch):
    seen = {}

    class Done:
        returncode = 0
        stdout = b""
        stderr = b""

    def record(argv, **kwargs):
        seen["argv"], seen["env"] = argv, kwargs["env"]
        return Done()

    monkeypatch.setattr(gitio.subprocess, "run", record)
    gitio._git(tmp_path, "ls-files", "-s", "-z")
    assert "--no-optional-locks" in seen["argv"]
    assert seen["env"]["GIT_OPTIONAL_LOCKS"] == "0"


def test_blob_id_matches_git_without_writing_objects():
    import subprocess

    data = b"print('hello')\n"
    expected = (
        subprocess.run(
            ["git", "hash-object", "--stdin"], input=data, capture_output=True, check=True
        )
        .stdout.decode()
        .strip()
    )
    assert gitio.git_blob_sha(data) == expected
