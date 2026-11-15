import pytest
from archgraph.paths import content_exclusion, looks_binary, path_exclusion


@pytest.mark.parametrize(
    "path, reason",
    [
        (".env", "SENSITIVE_FILENAME"),
        ("config/.env.production", "SENSITIVE_FILENAME"),
        ("keys/server.pem", "SENSITIVE_FILENAME"),
        ("id_rsa", "SENSITIVE_FILENAME"),
        ("secrets.yaml", "SENSITIVE_FILENAME"),
        (".ssh/config", "DENIED_DIRECTORY"),
        (".ccc-mcp/tunnel/credentials.json", "DENIED_DIRECTORY"),
        ("node_modules/x/index.js", "DENIED_DIRECTORY"),
        ("../outside.py", "PATH_OUTSIDE_REPOSITORY"),
        ("/etc/passwd", "PATH_OUTSIDE_REPOSITORY"),
        ("notes/cli.py", None),
        ("notes/secret_utils.py", None),
    ],
)
def test_path_exclusion_is_decided_from_the_name(path, reason):
    assert path_exclusion(path) == reason


def test_content_with_an_assigned_credential_is_flagged_without_returning_it():
    data = b'API_KEY = "sk-demo-0000000000000000"\n'
    reason = content_exclusion(data)
    assert reason == "CONTENT_SECRET_SUSPECT:ASSIGNED_SECRET"
    assert "sk-demo" not in reason


def test_private_key_block_and_aws_key_are_flagged():
    assert (
        content_exclusion(b"-----BEGIN RSA PRIVATE KEY-----\nabc")
        == "CONTENT_SECRET_SUSPECT:PRIVATE_KEY_BLOCK"
    )
    assert (
        content_exclusion(b"x = 'AKIAABCDEFGHIJKLMNOP'")
        == "CONTENT_SECRET_SUSPECT:AWS_ACCESS_KEY_ID"
    )


def test_ordinary_code_is_not_flagged():
    assert content_exclusion(b"def add(a, b):\n    return a + b\n") is None


def test_binary_detection():
    assert looks_binary(b"\x89PNG\x00\x00")
    assert not looks_binary(b"plain text")
