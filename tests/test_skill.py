from __future__ import annotations

import re
import shutil
from pathlib import Path
from typing import Any

import httpx
import pytest
import yaml

import fairvenue

SKILL = Path(__file__).resolve().parents[1] / "skills" / "fairvenue"


def test_skill_is_installable_without_the_sdk_checkout(tmp_path: Path) -> None:
    installed = tmp_path / "fairvenue"
    shutil.copytree(SKILL, installed)
    frontmatter = installed.joinpath("SKILL.md").read_text().split("---", 2)[1]
    metadata = yaml.safe_load(frontmatter)
    assert metadata["name"] == installed.name
    assert isinstance(metadata["description"], str) and metadata["description"]
    interface = yaml.safe_load(installed.joinpath("agents/openai.yaml").read_text())["interface"]
    assert "$fairvenue" in interface["default_prompt"]
    assert 25 <= len(interface["short_description"]) <= 64

    # A skills installer copies only this folder. Every local reference must survive that copy.
    for document in installed.rglob("*.md"):
        for link in re.findall(r"\]\(([^)]+)\)", document.read_text()):
            if link.startswith("https://"):
                continue
            target = (document.parent / link.split("#", 1)[0]).resolve()
            assert target.is_relative_to(installed), f"Reference escapes skill bundle: {document}"
            assert target.is_file(), f"Missing installed reference in {document}"


@pytest.mark.parametrize("environment", ["testnet", "mainnet"])
def test_skill_dry_run_checks_environment_without_credentials_or_orders(
    monkeypatch: Any, capsys: Any, environment: str
) -> None:
    requests: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        import json

        assert request.url.path == "/api/v1/info"
        assert "authorization" not in request.headers
        kind = json.loads(request.content)["type"]
        requests.append(kind)
        if kind == "meta":
            return httpx.Response(
                200,
                json={
                    "api_version": "v1",
                    "environment": environment,
                    "settlement": "virtual",
                    "markets": [{"market_id": 0}],
                },
            )
        assert kind == "bbo"
        return httpx.Response(200, json={"bid": "99999.9", "ask": "100000.0"})

    client = fairvenue.FairVenueClient(
        "https://testnet.example", transport=httpx.MockTransport(handler)
    )
    monkeypatch.setenv("FAIRVENUE_API_URL", "https://testnet.example")
    monkeypatch.setattr(fairvenue, "FairVenueClient", lambda _: client)
    reference = SKILL.joinpath("references/setup.md").read_text()
    snippet = reference.split("```python\n", 1)[1].split("```", 1)[0]
    if environment == "testnet":
        exec(compile(snippet, "skill-dry-run", "exec"), {})  # noqa: S102
        assert requests == ["meta", "bbo"]
        assert "'dry_run': True" in capsys.readouterr().out
    else:
        with pytest.raises(RuntimeError, match="Unexpected API environment"):
            exec(compile(snippet, "skill-dry-run", "exec"), {})  # noqa: S102
        assert requests == ["meta"]
