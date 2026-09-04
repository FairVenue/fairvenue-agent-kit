"""Minimal secure local setup CLI."""

from __future__ import annotations

import argparse
import ipaddress
import secrets
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

from .client import FairVenueClient
from .credentials import Credentials
from .errors import FairVenueError
from .signing import public_key


def _is_loopback(url: str) -> bool:
    hostname = urlparse(url).hostname
    if hostname == "localhost":
        return True
    try:
        return bool(hostname and ipaddress.ip_address(hostname).is_loopback)
    except ValueError:
        return False


def _response(response: httpx.Response) -> dict[str, Any]:
    response.raise_for_status()
    value = response.json()
    if not isinstance(value, dict):
        raise FairVenueError("API response must be a JSON object")
    return value


def init_local(args: argparse.Namespace) -> int:
    if not _is_loopback(args.base_url):
        raise FairVenueError("init-local is restricted to loopback API URLs")
    destination = Path(args.credentials)
    if destination.exists():
        raise FairVenueError("credential file already exists")
    seed = secrets.token_hex(32)
    with httpx.Client(base_url=args.base_url.rstrip("/"), timeout=10.0) as client:
        created = _response(
            client.post(
                "/api/v1/testnet/reset",
                json={"public_key": public_key(seed)},
            )
        )
        account_id = str(created["account_id"])
        agent_key_id = str(created["agent_key_id"])
        _response(
            client.post(
                "/api/v1/testnet/faucet",
                json={"account_id": account_id, "amount": args.virtual_usdc},
            )
        )
    credentials = Credentials(
        api_url=args.base_url.rstrip("/"),
        environment="testnet",
        account_id=account_id,
        agent_key_id=agent_key_id,
        private_key_seed=seed,
    )
    credentials.write_new(destination)
    print(f"Created virtual account {account_id}")
    print(f"Registered agent key {agent_key_id}")
    print(f"Credentials written to {destination} with owner-only permissions")
    return 0


def verify(args: argparse.Namespace) -> int:
    credentials = Credentials.from_file(args.credentials)
    with FairVenueClient.from_credentials(credentials) as client:
        meta = client.info({"type": "meta"})
        if meta.get("api_version") != "v1" or meta.get("environment") != credentials.environment:
            raise FairVenueError("server metadata does not match the credential environment")
        account = client.info({"type": "account_state"})
    print(
        f"Connected to {meta.get('environment')} API {meta.get('api_version')} "
        f"for {credentials.account_id}"
    )
    print(f"Virtual account state available: {bool(account)}")
    return 0


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="fairvenue")
    commands = root.add_subparsers(dest="command", required=True)

    local = commands.add_parser("init-local", help="create local virtual testnet credentials")
    local.add_argument("--base-url", default="http://127.0.0.1:8088")
    local.add_argument("--credentials", required=True)
    local.add_argument("--virtual-usdc", default="100000")
    local.set_defaults(handler=init_local)

    check = commands.add_parser("verify", help="authenticate and verify server metadata")
    check.add_argument("--credentials", required=True)
    check.set_defaults(handler=verify)
    return root


def main() -> None:
    arguments = parser().parse_args()
    try:
        raise SystemExit(arguments.handler(arguments))
    except (FairVenueError, httpx.HTTPError) as error:
        raise SystemExit(f"fairvenue: {error}") from None
