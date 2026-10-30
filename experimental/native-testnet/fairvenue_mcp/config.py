"""Environment configuration for the FairVenue MCP server.

Read once at startup. Nothing here touches the network or the filesystem;
directories are created later, only by an explicit setup tool call.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path

#: SHA256 of the deployment pins packaged at this commit
#: (``fairvenue_schema6/deployments/arbitrum-sepolia-1k.json``). The override
#: ``FAIRVENUE_INSTALLATION_SHA256`` only selects a separately reviewed pin file
#: for the same settlement (for example new gateway keys): the SDK hard-codes the
#: settlement and mock token, so a new deployment needs an updated kit.
PACKAGED_INSTALLATION_SHA256 = "9a39af143e67f4675f05998bedbafb02a052819d23a2129674db8f8588938d4d"

DEFAULT_HOME = "~/.fairvenue/native-testnet"
DEFAULT_DOWNLOAD_DIRS = ("~/Downloads",)

DEFAULT_MAX_ORDER_QUANTITY = Decimal("0.01")
DEFAULT_MAX_ORDER_NOTIONAL = Decimal("1000")
DEFAULT_MAX_PRICE_DEVIATION_BPS = Decimal("500")

_ON = {"1", "true", "yes", "on", "enabled"}
_OFF = {"0", "false", "no", "off", "disabled"}
_ASK = {"", "ask"}


class ConfigError(ValueError):
    """A configuration value the server refuses to guess about."""


@dataclass(frozen=True)
class Limits:
    """Local fat-finger guards. The server's own margin checks still apply."""

    max_order_quantity: Decimal
    max_order_notional: Decimal
    max_price_deviation_bps: Decimal


@dataclass(frozen=True)
class Config:
    home: Path
    installation_file: Path
    installation_sha256: str
    key_file: Path | None
    #: "ask" (default): the user confirms trading once per server session in a dialog.
    #: "on": trading is allowed from the start. "off": never, whatever the user clicks.
    trading_mode: str
    limits: Limits
    download_dirs: tuple[Path, ...]
    #: Whether connect_account may open the key page in the desktop browser.
    open_browser: bool = True

    @property
    def keys_dir(self) -> Path:
        return self.home / "keys"

    @property
    def continuity_root(self) -> Path:
        return self.home / "continuity"

    @property
    def journal_file(self) -> Path:
        return self.home / "journal.jsonl"

    @property
    def state_file(self) -> Path:
        return self.home / "state.json"


def _path(value: str) -> Path:
    # Absolute and normalized, but symlinks are NOT resolved: the SDK refuses
    # symlinked key, pin and continuity paths, and so must this server.
    return Path(os.path.normpath(os.path.abspath(os.path.expanduser(value))))


def _positive(environ: Mapping[str, str], name: str, default: Decimal) -> Decimal:
    raw = environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = Decimal(raw)
    except InvalidOperation:
        raise ConfigError(f"{name} must be a positive decimal, for example {default}") from None
    if not value.is_finite() or value <= 0:
        raise ConfigError(f"{name} must be a positive decimal, for example {default}")
    return value


def load_config(environ: Mapping[str, str] | None = None) -> Config:
    """Build the configuration from ``FAIRVENUE_*`` environment variables."""
    env = os.environ if environ is None else environ
    home = _path(env.get("FAIRVENUE_HOME", "").strip() or DEFAULT_HOME)
    installation = env.get("FAIRVENUE_INSTALLATION_FILE", "").strip()
    sha256 = env.get("FAIRVENUE_INSTALLATION_SHA256", "").strip().lower()
    if not sha256:
        sha256 = PACKAGED_INSTALLATION_SHA256
    if len(sha256) != 64 or any(char not in "0123456789abcdef" for char in sha256):
        raise ConfigError("FAIRVENUE_INSTALLATION_SHA256 must be 64 lowercase hex characters")
    key = env.get("FAIRVENUE_KEY_FILE", "").strip()
    trading = env.get("FAIRVENUE_TRADING", "").strip().lower()
    if trading not in _ON | _OFF | _ASK:
        raise ConfigError("FAIRVENUE_TRADING must be ask, on or off")
    downloads = env.get("FAIRVENUE_DOWNLOAD_DIRS", "").strip()
    browser = env.get("FAIRVENUE_OPEN_BROWSER", "").strip().lower()
    if browser not in _ON | _OFF | {""}:
        raise ConfigError("FAIRVENUE_OPEN_BROWSER must be on or off")
    return Config(
        home=home,
        installation_file=_path(installation) if installation else home / "installation.json",
        installation_sha256=sha256,
        key_file=_path(key) if key else None,
        trading_mode="on" if trading in _ON else "off" if trading in _OFF else "ask",
        limits=Limits(
            max_order_quantity=_positive(
                env, "FAIRVENUE_MAX_ORDER_QUANTITY", DEFAULT_MAX_ORDER_QUANTITY
            ),
            max_order_notional=_positive(
                env, "FAIRVENUE_MAX_ORDER_NOTIONAL", DEFAULT_MAX_ORDER_NOTIONAL
            ),
            max_price_deviation_bps=_positive(
                env, "FAIRVENUE_MAX_PRICE_DEVIATION_BPS", DEFAULT_MAX_PRICE_DEVIATION_BPS
            ),
        ),
        download_dirs=tuple(
            _path(item)
            for item in (downloads.split(os.pathsep) if downloads else DEFAULT_DOWNLOAD_DIRS)
            if item.strip()
        ),
        open_browser=browser not in _OFF,
    )
