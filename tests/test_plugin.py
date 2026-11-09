"""The skill, MCP server and plugin manifests stay one consistent package."""

from __future__ import annotations

import hashlib
import json
import re
import tomllib
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
NATIVE = ROOT / "experimental/native-testnet"


def load(path: str) -> Any:
    return json.loads((ROOT / path).read_text())


def test_versions_and_names_agree() -> None:
    claude = load(".claude-plugin/plugin.json")
    portable = load("plugin.json")
    project = tomllib.loads((NATIVE / "pyproject.toml").read_text())["project"]
    server = re.search(
        r'__version__ = "([^"]+)"', (NATIVE / "fairvenue_mcp/__init__.py").read_text()
    )
    assert server is not None
    assert claude["version"] == portable["version"] == project["version"] == server.group(1)
    assert claude["name"] == portable["name"] == "fairvenue"
    assert portable["$schema"] == "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json"
    assert "license" not in claude and "license" not in portable  # Not chosen without a request.
    marketplace = load(".claude-plugin/marketplace.json")
    assert marketplace["name"] == "fairvenue"
    assert marketplace["plugins"] == [
        {**marketplace["plugins"][0], "name": "fairvenue", "source": "./"}
    ]
    assert (ROOT / "skills/fairvenue/SKILL.md").is_file()


def test_every_launcher_runs_the_same_packaged_server() -> None:
    project = tomllib.loads((NATIVE / "pyproject.toml").read_text())["project"]
    assert project["scripts"]["fairvenue-mcp"] == "fairvenue_mcp.cli:main"
    assert [
        dependency.split("==")[0] for dependency in project["optional-dependencies"]["mcp"]
    ] == ["mcp"]
    claude = load(".claude-plugin/plugin.json")["mcpServers"]["fairvenue"]
    assert claude["command"] == "uvx"
    assert claude["args"] == [
        "--from",
        "${CLAUDE_PLUGIN_ROOT}/experimental/native-testnet[mcp]",
        "fairvenue-mcp",
    ]
    # Zero configuration: trading is allowed in a one-click dialog, not in plugin settings.
    assert "userConfig" not in load(".claude-plugin/plugin.json")
    assert "env" not in claude
    assert not re.search(r"\$\{user_config\.", json.dumps(load(".claude-plugin/plugin.json")))
    portable = load("mcp.json")
    assert portable["$schema"] == "https://agent-plugins.org/schemas/1.0.0/mcp.schema.json"
    assert portable["mcpServers"]["fairvenue"] == {
        "type": "stdio",
        "command": "uvx",
        "args": ["--from", "${PLUGIN_ROOT}/experimental/native-testnet[mcp]", "fairvenue-mcp"],
    }
    # No project-scoped .mcp.json: it would break for anyone developing this repository.
    assert not (ROOT / ".mcp.json").exists()


def test_packaged_deployment_pin_is_the_one_everything_names() -> None:
    pins = NATIVE / "fairvenue_schema6/deployments/arbitrum-sepolia-ts2.json"
    digest = hashlib.sha256(pins.read_bytes()).hexdigest()
    config = (NATIVE / "fairvenue_mcp/config.py").read_text()
    assert f'PACKAGED_INSTALLATION_SHA256 = "{digest}"' in config
    source = json.loads((NATIVE / "SOURCE.json").read_text())
    imported = {entry["path"]: entry["sha256"] for entry in source["byte_identical"]}
    assert imported["fairvenue_schema6/deployments/arbitrum-sepolia-ts2.json"] == digest
    assert not (NATIVE / "fairvenue_schema6/deployments/arbitrum-sepolia-1k.json").exists()
    guide = (ROOT / "skills/fairvenue/references/native-testnet.md").read_text()
    assert digest in guide
    deployment = json.loads(pins.read_bytes())
    for pin in (
        deployment["settlement"],
        deployment["config_hash"],
        "fairvenue-ts2-testnet-agent-v1",
    ):
        assert pin in guide, pin
    for document in ("docs/MCP.md", "experimental/native-testnet/README.md"):
        assert "TS2: create a new bot key" in (ROOT / document).read_text(), document


def test_hackathon_facts_match_between_skill_and_server() -> None:
    skill = " ".join((ROOT / "skills/fairvenue/references/hackathon.md").read_text().split())
    guide = (NATIVE / "fairvenue_mcp/guide.py").read_text().replace("\\\n", "")
    guide = " ".join(guide.split())
    for fact in (
        "November 16, 17 and 18, 2026 (UTC). Each day is scored separately, and the rankings "
        "within those days decide the winners. Only trading on those days is ranked.",
        "the account that traded more before the scoring days ranks higher.",
        "Submissions close Nov 18, 2026, 23:59 UTC.",
        "Requires PnL > 0 and Sharpe > 1.",
        "Requires Sharpe > 2.",
        "$1,005 in total, paid in crypto",
        "multiplied by √8760",
        "that day's 24 hourly returns with non-zero variance",
    ):
        assert fact in skill, fact
        assert fact in guide, fact
