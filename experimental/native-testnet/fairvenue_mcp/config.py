"""Environment configuration for the FairVenue MCP server.

Read once at startup. Nothing here touches the network or writes the filesystem;
directories are created later, only by an explicit setup tool call. The one
exception is the user's trading switch in ``~/.fairvenue/settings.json``, which
``read_trading_setting`` reads at every signed call.
"""

from __future__ import annotations

import errno
import json
import os
import stat
from collections.abc import Mapping
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from pathlib import Path

#: SHA256 of the TS2 deployment pins packaged at this commit
#: (``fairvenue_schema6/deployments/arbitrum-sepolia-ts2.json``). The override
#: ``FAIRVENUE_INSTALLATION_SHA256`` only selects a separately reviewed pin file
#: for the same deployment (for example another approved API origin): the SDK
#: hard-codes the TS2 settlement and config hash, so a new deployment needs an
#: updated kit.
PACKAGED_INSTALLATION_SHA256 = "ba25da81e3ebc9f6679a184993dbc4a64fd089684f54e174e8ad654b725232d4"

#: A fresh profile for TS2 season 2. Retired profiles are never read, changed or
#: deleted: their keys and pending originals cannot act on season 2.
DEFAULT_HOME = "~/.fairvenue/ts2-season2"
LEGACY_V4_HOME = "~/.fairvenue/native-testnet"
#: The TS2 season 1 profile (settlement 0x3beb98f3…) of kit 0.3.0.
TS2_SEASON1_HOME = "~/.fairvenue/ts2-testnet"
DEFAULT_DOWNLOAD_DIRS = ("~/Downloads",)

DEFAULT_MAX_ORDER_QUANTITY = Decimal("0.01")
DEFAULT_MAX_ORDER_NOTIONAL = Decimal("1000")
DEFAULT_MAX_PRICE_DEVIATION_BPS = Decimal("500")
#: Browser parity: a resting (GTC) order lives 23 hours. The server allows 24.
DEFAULT_GTC_LIFETIME_MINUTES = 23 * 60
MAX_GTC_LIFETIME_MINUTES = 23 * 60

_ON = {"1", "true", "yes", "on", "enabled"}
_OFF = {"0", "false", "no", "off", "disabled"}

#: The trading switch that works in every client. Clients pass HOME to the server,
#: but not always other variables (Codex passes plugin servers a short fixed list).
#: FAIRVENUE_TRADING, when set, wins over it.
SETTINGS_FILE = "~/.fairvenue/settings.json"
#: The command that turns trading on for this computer. ``rm`` of the file undoes it.
ENABLE_TRADING_COMMAND = (
    'mkdir -p ~/.fairvenue && printf \'{"trading":"on"}\\n\' > ~/.fairvenue/settings.json '
    "&& chmod 600 ~/.fairvenue/settings.json"
)
TRADING_SETTINGS = ("on", "ask", "off")
_SETTINGS_MAX_BYTES = 4096


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
    #: FAIRVENUE_TRADING, or None when it is not set and the settings file decides.
    #: "ask" (the default): the user confirms trading once per server session in a
    #: dialog, or on a browser page when the client cannot show it.
    #: "on": trading is allowed from the start. "off": never, whatever the user clicks.
    trading_mode: str | None
    limits: Limits
    download_dirs: tuple[Path, ...]
    #: Whether connect_account may open the key page, and the trading confirmation its
    #: page, in the desktop browser.
    open_browser: bool = True
    #: How long a GTC order rests unless cancelled or filled.
    gtc_lifetime_ms: int = DEFAULT_GTC_LIFETIME_MINUTES * 60_000
    #: The user's trading switch, read at every signed call.
    settings_file: Path = field(default_factory=lambda: Path.home() / ".fairvenue/settings.json")

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


def _minutes(environ: Mapping[str, str], name: str, default: int, maximum: int) -> int:
    raw = environ.get(name, "").strip()
    if not raw:
        return default
    if not raw.isdigit() or not 1 <= int(raw) <= maximum:
        raise ConfigError(f"{name} must be a whole number of minutes from 1 to {maximum}")
    return int(raw)


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
    if trading not in _ON | _OFF | {"", "ask"}:
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
        trading_mode="on" if trading in _ON else "off" if trading in _OFF else trading or None,
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
        gtc_lifetime_ms=_minutes(
            env,
            "FAIRVENUE_GTC_LIFETIME_MINUTES",
            DEFAULT_GTC_LIFETIME_MINUTES,
            MAX_GTC_LIFETIME_MINUTES,
        )
        * 60_000,
    )


def read_trading_setting(path: Path) -> tuple[str | None, str | None]:
    """The "trading" value of the user's settings file.

    Returns (value, None), (None, None) when there is no file, or (None, problem)
    when the file is ignored. Only a regular file that the current user owns and
    nobody else can write counts, so a broken or planted file never allows trading.
    The problem names the file, never its contents.
    """
    try:
        # O_NONBLOCK: a FIFO in its place must not hang the server.
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return None, None
    except OSError as error:
        reason = "is a symlink" if error.errno == errno.ELOOP else "is unreadable"
        return None, _ignored(path, reason)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            return None, _ignored(path, "is not a regular file")
        if info.st_uid != os.getuid():
            return None, _ignored(path, "belongs to another user")
        if info.st_mode & 0o022:
            return None, _ignored(path, "is writable by other users. Run chmod 600 on it")
        data = os.read(fd, _SETTINGS_MAX_BYTES + 1)
    finally:
        os.close(fd)
    try:
        document = json.loads(data) if len(data) <= _SETTINGS_MAX_BYTES else None
    except (ValueError, RecursionError):
        return None, _ignored(path, "is not valid JSON")
    value = document.get("trading") if isinstance(document, dict) else None
    if value not in TRADING_SETTINGS:
        return None, _ignored(path, 'does not hold {"trading": "on"}, "ask" or "off"')
    return value, None


def _ignored(path: Path, reason: str) -> str:
    return f"{path} is ignored because it {reason}. Trading falls back to ask."
