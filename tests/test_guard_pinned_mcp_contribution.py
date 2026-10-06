"""Exact npm versions select contributed defaults, never saved approval."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from codex_plugin_scanner.guard.mcp_tool_calls import build_tool_call_artifact
from codex_plugin_scanner.guard.runtime import mcp_server_grants as grants
from codex_plugin_scanner.guard.runtime.mcp_protection import build_mcp_server_identity
from codex_plugin_scanner.guard.runtime.mcp_server_catalog import _values_for_payload
from codex_plugin_scanner.guard.runtime.mcp_server_contribution import mcp_tool_state, validate_mcp_contribution

ROOT = Path(__file__).resolve().parents[1]


def payload():
    return json.loads((ROOT / "contributions/mcp-servers/mcp.lattice-talk.json").read_bytes())


def artifact(package="lattice-talk@0.1.18", command="npx", extra=(), transport="stdio"):
    identity = build_mcp_server_identity(config_path="", command=command, args=(*extra, package), transport=transport)
    return build_tool_call_artifact(
        harness="codex",
        server_name="lattice-talk",
        tool_name="join_session",
        source_scope="project",
        config_path=".mcp.json",
        transport=transport,
        server_identity=identity,
    )


@pytest.mark.parametrize(
    "tool",
    [
        "tell_agent",
        "tell_room",
        "create_room",
        "memory_set",
        "memory_note",
        "join_session",
        "join_room",
        "leave_session",
        "pull_messages",
    ],
)
def test_shared_state_mutations_require_review(tool):
    assert mcp_tool_state(payload(), tool) == "review"


@pytest.mark.parametrize(
    "tool", ["list_peers", "session_info", "memory_get", "memory_list", "memory_notes", "trace_context", "other"]
)
def test_read_only_and_existing_fallback_defaults_are_unchanged(tool):
    assert mcp_tool_state(payload(), tool) == "inherit"


@pytest.mark.parametrize("version", ["", "latest", "^0.1.18", "~0.1.18", "*", "0.1", "00.1.18", "0.1.18\n", None])
def test_pin_rejects_tags_ranges_and_noncanonical_versions(version):
    value = payload()
    value["launch"]["packageVersion"] = version
    with pytest.raises(ValueError):
        validate_mcp_contribution(value)


@pytest.mark.parametrize("command", ["uvx", "pipx"])
def test_npm_pin_rejects_other_ecosystem_launchers(command):
    value = payload()
    value["launch"]["command"] = command
    with pytest.raises(ValueError, match="npm launcher"):
        validate_mcp_contribution(value)


@pytest.mark.parametrize("package", ["lattice-talk@0.1.18", "lattice-talk --help", "https://example.com/pkg", "Pkg"])
def test_pin_requires_canonical_npm_package_name(package):
    value = payload()
    value["launch"]["package"] = package
    with pytest.raises(ValueError, match="canonical package"):
        validate_mcp_contribution(value)


def test_exact_pin_matches_and_projects_runnable_example(monkeypatch):
    value = payload()
    validate_mcp_contribution(value)
    monkeypatch.setattr(grants, "load_mcp_contribution_payloads", lambda: (value,))
    assert grants.matching_mcp_contribution(artifact()) == value
    assert {permission.example_command for permission in _values_for_payload(value)["permissions"]} == {
        "npx -y lattice-talk@0.1.18"
    }


@pytest.mark.parametrize(
    "package", ["lattice-talk", "lattice-talk@latest", "lattice-talk@0.1.17", "lattice-talk@^0.1.18", "other@0.1.18"]
)
def test_unpinned_or_mismatched_release_does_not_receive_catalog_defaults(monkeypatch, package):
    monkeypatch.setattr(grants, "load_mcp_contribution_payloads", lambda: (payload(),))
    assert grants.matching_mcp_contribution(artifact(package)) is None


@pytest.mark.parametrize(
    "command,extra,transport",
    [
        ("bunx", (), "stdio"),
        ("npx", ("--registry", "https://unreviewed.example"), "stdio"),
        ("npx", (), "http"),
    ],
)
def test_pin_does_not_transfer_to_other_launcher_registry_or_transport(monkeypatch, command, extra, transport):
    monkeypatch.setattr(grants, "load_mcp_contribution_payloads", lambda: (payload(),))
    assert grants.matching_mcp_contribution(artifact(command=command, extra=extra, transport=transport)) is None


@pytest.mark.parametrize("missing", ["package_version", "package_source", "command", "mcp_tool_identity"])
def test_missing_identity_evidence_does_not_match(monkeypatch, missing):
    monkeypatch.setattr(grants, "load_mcp_contribution_payloads", lambda: (payload(),))
    tool = artifact()
    metadata = dict(tool.metadata)
    if missing == "mcp_tool_identity":
        metadata.pop(missing)
    else:
        identity = dict(metadata["mcp_server_identity"])
        identity.pop(missing)
        metadata["mcp_server_identity"] = identity
    assert grants.matching_mcp_contribution(replace(tool, metadata=metadata)) is None


def test_unversioned_existing_contributions_remain_compatible(monkeypatch):
    value = payload()
    value["launch"].pop("packageVersion")
    monkeypatch.setattr(grants, "load_mcp_contribution_payloads", lambda: (value,))
    assert grants.matching_mcp_contribution(artifact("lattice-talk")) == value


@pytest.mark.parametrize("tool_name", ["join_session", "join_room", "leave_session", "pull_messages"])
def test_mutation_defaults_stay_inert_until_local_enable(monkeypatch, tool_name):
    from codex_plugin_scanner.guard.runtime.extension_control_contract import ControlLayerKind, ControlState

    from .test_guard_mcp_server_grants import _AuthorityStore, _layer

    monkeypatch.setattr(grants, "load_mcp_contribution_payloads", lambda: (payload(),))
    tool = artifact()
    metadata = dict(tool.metadata)
    metadata["mcp_tool_identity"] = {**metadata["mcp_tool_identity"], "tool_name": tool_name}
    tool = replace(tool, metadata=metadata)
    assert grants.apply_contributed_mcp_decision(_AuthorityStore(), tool, "allow") is None
    cloud = _AuthorityStore((_layer(ControlLayerKind.SIGNED_CLOUD, "command.mcp-lattice-talk", ControlState.ENABLED),))
    assert grants.apply_contributed_mcp_decision(cloud, tool, "allow") is None
    local = _AuthorityStore((_layer(ControlLayerKind.LOCAL_ADMIN, "command.mcp-lattice-talk", ControlState.ENABLED),))
    assert grants.apply_contributed_mcp_decision(local, tool, "allow")[0] == "review"
    assert grants.apply_contributed_mcp_decision(local, tool, "block") is None
