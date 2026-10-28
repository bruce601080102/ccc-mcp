"""Tests for the local, opt-in "remember this tunnel's runtime key" storage used
by both `ccc-mcp connect --remember` and the web console's Connect page.
"""

import stat
import sys

import pytest

from ccc_mcp.adapters.tunnel_client import (
    credentials_path,
    forget_tunnel_key,
    load_tunnel_key,
    remembered_tunnels,
    save_tunnel_key,
    validate_api_key,
    validate_tunnel_id,
)
from ccc_mcp.infra.errors import BridgeError

TUNNEL = "tunnel_" + "a" * 32


def test_save_then_load_round_trips(tmp_path):
    assert load_tunnel_key(tmp_path, TUNNEL) is None
    save_tunnel_key(tmp_path, TUNNEL, "secret-key-1")
    assert load_tunnel_key(tmp_path, TUNNEL) == "secret-key-1"


def test_save_overwrites_a_previous_key_for_the_same_tunnel(tmp_path):
    save_tunnel_key(tmp_path, TUNNEL, "first")
    save_tunnel_key(tmp_path, TUNNEL, "second")
    assert load_tunnel_key(tmp_path, TUNNEL) == "second"


def test_remembers_multiple_tunnels_independently(tmp_path):
    other = "tunnel_" + "b" * 32
    save_tunnel_key(tmp_path, TUNNEL, "key-a")
    save_tunnel_key(tmp_path, other, "key-b")
    assert load_tunnel_key(tmp_path, TUNNEL) == "key-a"
    assert load_tunnel_key(tmp_path, other) == "key-b"
    assert remembered_tunnels(tmp_path) == sorted([TUNNEL, other])


def test_forget_removes_only_the_named_tunnel(tmp_path):
    other = "tunnel_" + "b" * 32
    save_tunnel_key(tmp_path, TUNNEL, "key-a")
    save_tunnel_key(tmp_path, other, "key-b")
    assert forget_tunnel_key(tmp_path, TUNNEL) is True
    assert load_tunnel_key(tmp_path, TUNNEL) is None
    assert load_tunnel_key(tmp_path, other) == "key-b"


def test_forget_an_unsaved_tunnel_is_a_safe_no_op(tmp_path):
    assert forget_tunnel_key(tmp_path, TUNNEL) is False


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permission bits")
def test_credentials_file_is_not_group_or_world_readable(tmp_path):
    save_tunnel_key(tmp_path, TUNNEL, "secret-key")
    mode = stat.S_IMODE(credentials_path(tmp_path).stat().st_mode)
    assert mode & (stat.S_IRWXG | stat.S_IRWXO) == 0


def test_validate_tunnel_id_rejects_malformed_values():
    with pytest.raises(BridgeError):
        validate_tunnel_id("not-a-tunnel-id")
    with pytest.raises(BridgeError):
        validate_tunnel_id("")
    validate_tunnel_id(TUNNEL)  # does not raise


def test_validate_api_key_rejects_empty_whitespace_and_tunnel_id_shaped_values():
    with pytest.raises(BridgeError):
        validate_api_key("")
    with pytest.raises(BridgeError):
        validate_api_key("has a space")
    with pytest.raises(BridgeError):
        validate_api_key(TUNNEL)
    validate_api_key("sk-real-looking-key")  # does not raise
