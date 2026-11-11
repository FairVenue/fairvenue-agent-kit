"""The FairVenue TS2 native TESTNET agent behind the MCP tools.

One process owns one agent key at a time: the SDK client holds that key's
continuity lock, and every signed operation runs under one asyncio lock, so
requests never race for a nonce. Nothing new is signed while ANY continuity
namespace holds a request whose outcome is unknown, so switching keys cannot
bypass recovery. Public reads are cached and paced because their lane is shared.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import subprocess
import sys
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from fairvenue_schema6 import AgentError, HttpTransport, NativeTestnetClient, ts2

from . import __version__, profile
from .config import LEGACY_V4_HOME, TS2_SEASON1_HOME, Config, Limits
from .journal import Journal
from .market import (
    SYMBOL,
    Market,
    MarketDataError,
    fmt,
    history_view,
    market_view,
    parse_history,
    parse_market,
    round_to_step,
    utc,
)
from .profile import SetupError
from .public import PublicApi, PublicApiError

#: Browser parity: a resting order lives up to 23 hours, an IOC order and an
#: account command 30 seconds, never past the key's own expiry.
GTC_LIFETIME_MS = 23 * 60 * 60 * 1000
ONE_SHOT_LIFETIME_MS = 30_000
MIN_DMS_DELAY_MS = 2_000
MAX_DMS_DELAY_MS = 86_000_000
MAX_LEVERAGE = 100
PERMISSION = {"place": ts2.TRADE, "cancel": ts2.CANCEL, "dead_man_switch": ts2.DEAD_MAN_SWITCH}
_PROTOCOL_TIME = re.compile(r"[0-9]{1,16}")
_ORDER_ID = re.compile(r"0x[0-9a-f]{64}")
#: A key downloaded this recently, before the page was opened, is still picked up.
RECENT_DOWNLOAD_SECONDS = 1_800
#: The setup page is opened again only after this long without a download.
REOPEN_PAGE_SECONDS = 600

ACCOUNT_VISIBILITY = (
    "This server does not read balances, positions or fills. place_order returns each "
    "order's order_id, which cancel_order takes. Use get_journal for what this server "
    "submitted, get_market for public trades, and ask the user to check the Arena terminal "
    "for balances and positions."
)
TRADING_FORCED_OFF = (
    "Trading is switched off in this MCP server's configuration (FAIRVENUE_TRADING=off). "
    "Nothing was signed. Only the user can change that setting. dry_run=true previews an order."
)
TRADING_NEEDS_CONFIRMATION = (
    "Trading needs the user's confirmation and this client cannot show the confirmation "
    "dialog. Nothing was signed. The user can allow it by setting FAIRVENUE_TRADING=on in this "
    "MCP server's configuration and restarting it. dry_run=true previews an order."
)
TRADING_DECLINED = "The user did not allow trading. Nothing was signed."
PENDING_GUIDANCE = (
    "A signed request has an unknown outcome. Call recover_pending_request before anything "
    "else, even if a key has expired. Never resend, reprice or resign it."
)
MODIFY_UNSUPPORTED = (
    "Order changes are not available on TS2. Cancel the order with cancel_order and place a "
    "new one. Nothing was signed."
)

NOT_PLACED_GUIDANCE = {
    "invalid_request": "The server refused the request as invalid. Nothing was placed. Check "
    "that the key is the current TS2 bot key.",
    "rejected": "The exchange refused it before it took effect. Nothing was placed. Typical "
    "causes are margin, a post-only order that would cross, a reduce-only order with nothing "
    "to reduce, or a fee mode or leverage change while orders or a position are open.",
    "admission_closed": "Trading or account settings are paused right now. Nothing was "
    "placed. Retry later.",
    "rate_limited": "Rate limited. Nothing was placed. Wait a few seconds, then retry.",
}

FAILURE_GUIDANCE = {
    "unavailable": "The TESTNET API was unreachable or refused the call before admission.",
    "deadline": "The call timed out before admission.",
    "context_changed": "The signed context changed (key expired, new deployment or clock moved).",
    "response_invalid": "The server reply did not match the protocol, so nothing was assumed.",
    "signature_invalid": "A local signature check failed.",
    "crypto_unavailable": profile.CRYPTO_PROBLEM,
    "invalid_input": "The SDK refused the request fields or the key lacks a permission.",
    "invalid_order": "The price or size does not fit the market tick or step size.",
    "unsupported": MODIFY_UNSUPPORTED,
    "v4_key": profile.NEW_KEY,
    "previous_deployment": profile.PREVIOUS_DEPLOYMENT,
    "nonce_reused": "The command nonce moved. Nothing was placed. Submit again.",
    "busy": "An earlier call on this key is still finishing. Retry in a few seconds.",
}

RECOVERY_MEANINGS = {
    "accepted": "The exchange journaled it (soft, operator-trusted). The original is cleared.",
    "rejected": "The exchange refused it. Nothing executed. The original is cleared.",
    "not_placed": "It never reached the exchange journal and its expiry has passed, so it can "
    "never execute. Nothing executed. The original is cleared.",
    "not_settled": "Its batch was voided. It never takes effect. The original is cleared.",
    "unresolved": "Still unknown. The original stays retained. Check again later.",
}


class ToolProblem(Exception):
    """An anticipated failure whose message is safe to show to the model."""


ClientOpener = Callable[..., Any]
PublicFactory = Callable[[str, str], PublicApi]
#: (the action for a protocol time, its public summary, its lifetime in ms)
Prepared = tuple[Callable[[int], dict[str, Any]], dict[str, Any], int]


def default_opener(
    *,
    policy: dict[str, Any],
    grant: dict[str, Any],
    signer: Any,
    api_url: str,
    custody_path: str,
    initialize: bool,
) -> Any:
    return NativeTestnetClient(
        policy=policy,
        grant=grant,
        signer=signer,
        transport=HttpTransport(api_url[:-1]),
        approved_api_url=api_url,
        custody_path=custody_path,
        initialize=initialize,
    )


def open_in_browser(url: str) -> bool:
    """Open an https page in the user's desktop browser and report whether it was tried.

    Only desktop openers are used, never a console browser: this server's stdin
    and stdout carry the MCP protocol. The URL is one argument, never a shell string.
    """
    if not url.startswith("https://"):
        return False
    if sys.platform == "darwin":
        opener: str | None = "/usr/bin/open"
    elif sys.platform.startswith("linux"):
        desktop = os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")
        opener = shutil.which("xdg-open") if desktop else shutil.which("wslview")
    else:
        opener = None
    if opener is None:
        return False
    try:
        subprocess.Popen(  # noqa: S603 - fixed opener, URL built from the installed pins.
            [opener, url],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except OSError:
        return False
    return True


def _no_browser(url: str) -> bool:
    return False


def _decimal(value: str, name: str) -> Decimal:
    text = value.strip()
    try:
        number = Decimal(text)
    except InvalidOperation:
        raise ToolProblem(f'{name} must be a decimal string such as "0.001".') from None
    if not number.is_finite() or number <= 0 or "e" in text.lower():
        raise ToolProblem(f'{name} must be a positive plain decimal string such as "0.001".')
    return number


def _order_id(value: str) -> str:
    text = value.strip().lower()
    if _ORDER_ID.fullmatch(text) is None or int(text, 16) == 0:
        raise ToolProblem(
            "order_id must be the 0x order ID (64 hex characters) that place_order returned "
            "or the Arena terminal shows."
        )
    return text


def _now_ms(context: dict[str, Any]) -> int:
    """This machine's clock, never behind the server's last protocol time."""
    return max(int(time.time() * 1000), int(context["protocol_time_ms"]))


def _pending_view(item: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "agent_public_key",
        "action",
        "nonce",
        "expires_at",
        "request_sha256",
        "unreadable",
        "custody",
    )
    return {key: item[key] for key in keys if key in item}


@dataclass(frozen=True)
class OrderPlan:
    wire: dict[str, Any]
    view: dict[str, Any]
    lifetime_ms: int


class Trader:
    def __init__(
        self,
        config: Config,
        *,
        opener: ClientOpener | None = None,
        public_factory: PublicFactory | None = None,
        browser: Callable[[str], bool] | None = None,
    ) -> None:
        self.config = config
        self.journal = Journal(config.journal_file)
        self._opener = opener or default_opener
        self._public_factory = public_factory or (
            lambda url, settlement: PublicApi(url, settlement)
        )
        self._browser = browser or (open_in_browser if config.open_browser else _no_browser)
        self._public: tuple[PublicApi, str, str] | None = None
        self._client: Any = None
        self._client_key: str | None = None
        self._grant: dict[str, Any] | None = None
        self._lock = asyncio.Lock()
        # Trading consent lives only as long as this server process.
        self._consent: Limits | None = None
        self._paused = False
        self._page_opened_at: float | None = None

    # Shared helpers ---------------------------------------------------------------

    def close(self) -> None:
        if self._client is not None:
            try:
                self._client.close()
            except AgentError:
                pass  # A still-running call keeps the key fenced; the process is exiting.
            self._client = self._client_key = self._grant = None

    def _release(self) -> None:
        if self._client is not None:
            try:
                self._client.close()
            except AgentError:
                raise ToolProblem(FAILURE_GUIDANCE["busy"]) from None
            self._client = self._client_key = self._grant = None

    def _journal(self, kind: str, entry: dict[str, Any]) -> str | None:
        """Append to the journal. Returns a problem description instead of raising."""
        try:
            self.journal.append(kind, entry)
        except OSError as error:
            return (
                f"The journal {self.journal.path} could not be written "
                f"({error.strerror or 'error'}). Fix its permissions or free disk space."
            )
        return None

    def _public_api(self) -> tuple[PublicApi, str, str | None]:
        """Public reads use the installed pins, or the packaged ones before setup.

        Returns the API, the settlement and the installed config hash (None before setup).
        """
        try:
            installed = profile.load_installation(self.config)
            api_url, settlement = installed.api_url, installed.settlement
            config_hash: str | None = installed.config_hash
        except SetupError:
            pins = json.loads(profile.packaged_installation())
            api_url, settlement, config_hash = pins["api_url"], pins["settlement"], None
        if self._public is None or self._public[1] != api_url:
            self._public = (self._public_factory(api_url, settlement), api_url, settlement)
        return self._public[0], settlement, config_hash

    def _open_key(self, installed: Any, signer: Any, grant: dict[str, Any], path: Path) -> Any:
        """The SDK client for one key, opening (or first initializing) its continuity."""
        material = grant["material"]
        identity = f"{path}:{material['agent_public_key']}:{material['nonce']}"
        if self._client is not None and self._client_key == identity:
            return self._client
        self._release()
        custody, initialize = profile.prepare_continuity(self.config, grant)
        try:
            client = self._opener(
                policy=installed.policy,
                grant=grant,
                signer=signer,
                api_url=installed.api_url,
                custody_path=str(custody),
                initialize=initialize,
            )
        except AgentError:
            if initialize:
                profile.rollback_continuity(self.config, custody)
            if profile.custody_held_elsewhere(custody):
                raise SetupError(
                    f"Another process is using {custody} (for example a second MCP session or a "
                    "bot with the same key). Stop it, or give each process its own key."
                ) from None
            raise SetupError(
                profile.crypto_problem()
                or f"Could not open the continuity database {custody}. It may hold leftover or "
                "modified files. Inspect it and never delete it."
            ) from None
        self._client, self._client_key, self._grant = client, identity, grant
        if initialize:
            self._journal(
                "continuity_initialized",
                {"agent_public_key": material["agent_public_key"], "path": str(custody)},
            )
        return client

    async def _open(self) -> Any:
        """The SDK client for the active key."""
        installed = profile.load_installation(self.config)
        signer, grant, path = profile.load_active_key(self.config, installed)
        return self._open_key(installed, signer, grant, path)

    def _waiting(self, client: Any = None) -> list[dict[str, Any]]:
        """Every unresolved original across all keys. Fails closed."""
        waiting = profile.pending_originals(self.config)
        if not waiting and client is not None:
            try:
                if client.has_pending_request():
                    material = (self._grant or {}).get("material") or {}
                    waiting = [{"agent_public_key": material.get("agent_public_key")}]
            except AgentError:
                waiting = [{"unreadable": True}]
        return waiting

    # Trading switch -------------------------------------------------------------

    def trading_state(self) -> str:
        """on, confirmed, needs_confirmation or off."""
        if self.config.trading_mode == "off":
            return "off"
        if self._paused:
            return "needs_confirmation"
        if self.config.trading_mode == "on":
            return "on"
        return "confirmed" if self._consent is not None else "needs_confirmation"

    @property
    def limits(self) -> Limits:
        return self._consent or self.config.limits

    def grant_consent(self, limits: Limits) -> dict[str, Any]:
        """Record the user's confirmation from the dialog, for this server session only."""
        if self.config.trading_mode == "off":
            raise ToolProblem(TRADING_FORCED_OFF)
        self._consent, self._paused = limits, False
        self._journal("trading_confirmed", {"limits": self.limits_view()})
        return self.limits_view()

    def pause_trading(self) -> dict[str, Any]:
        self._consent, self._paused = None, True
        self._journal("trading_paused", {})
        return {"status": "paused", **self.limits_view()}

    def _require_trading(self) -> None:
        state = self.trading_state()
        if state == "off":
            raise ToolProblem(TRADING_FORCED_OFF)
        if state == "needs_confirmation":
            raise ToolProblem(TRADING_NEEDS_CONFIRMATION)

    def limits_view(self) -> dict[str, Any]:
        limits = self.limits
        return {
            "trading": self.trading_state(),
            "max_order_quantity_btc": fmt(limits.max_order_quantity),
            "max_order_notional_usdc": fmt(limits.max_order_notional),
            "max_price_deviation_bps": fmt(limits.max_price_deviation_bps),
        }

    # Setup ------------------------------------------------------------------------

    async def setup_status(self) -> dict[str, Any]:
        steps: list[str] = []
        status: dict[str, Any] = {
            "server": f"fairvenue-mcp {__version__}",
            "protocol": "TS2",
            "home": str(self.config.home),
            "trading": self.limits_view(),
        }
        retired = [
            {
                "path": str(path),
                "note": f"The retired {name} profile is kept untouched. Its keys cannot sign on "
                "TS2 season 2 and its requests cannot be recovered here. Create a new bot key.",
            }
            for name, path in (
                ("V4 testnet", Path(os.path.expanduser(LEGACY_V4_HOME))),
                ("TS2 season 1", Path(os.path.expanduser(TS2_SEASON1_HOME))),
            )
            if path != self.config.home and path.is_dir()
        ]
        if retired:
            status["previous_testnet_profiles"] = retired
        try:
            waiting = profile.pending_originals(self.config)
        except SetupError as problem:
            waiting = []
            status["state_problem"] = str(problem)
            steps.append("Fix the state.json problem above before anything else.")
        if waiting:
            status["pending_requests"] = [_pending_view(item) for item in waiting]
            steps.append(PENDING_GUIDANCE)
        connect = (
            "Call connect_account. It opens the key page in the user's browser, picks up the "
            "downloaded key by itself and checks the connection."
        )
        try:
            installed = profile.load_installation(self.config)
            status["deployment"] = {"installed": True, **profile.pins_view(self.config, installed)}
        except SetupError as problem:
            installed = None
            status["deployment"] = {"installed": False, "problem": str(problem)}
            steps.append(connect)
        if installed is not None:
            try:
                _, grant, path = profile.load_active_key(self.config, installed)
            except SetupError as problem:
                status["agent_key"] = {"ready": False, "problem": str(problem)}
                steps.append(connect)
            else:
                key = profile.key_view(grant, path)
                status["agent_key"] = {"ready": not key["expired"], **key}
                if key["expired"]:
                    steps.append("The key expired. " + connect)
                custody = profile.custody_location(self.config, grant)
                ours = self._client is not None and self._client_key is not None
                ours = ours and str(self._client_key).startswith(f"{path}:")
                continuity: dict[str, Any] = {
                    "path": str(custody),
                    "initialized": custody.exists(),
                    "pending_request": any(str(custody) == item.get("custody") for item in waiting),
                }
                if custody.exists() and not ours and profile.custody_held_elsewhere(custody):
                    continuity["in_use_by_another_process"] = True
                    steps.append(
                        "Another process holds this key's continuity lock. Stop it, or give each "
                        "process its own key."
                    )
                status["continuity"] = continuity
                if not custody.exists() and not key["expired"]:
                    steps.append(connect)
        if self.trading_state() == "off":
            steps.append("Trading is switched off in this server's configuration (read-only).")
        status["account_visibility"] = ACCOUNT_VISIBILITY
        status["next_steps"] = steps or [
            "Ready. Read get_market. The first signed order asks the user once to allow trading."
            if self.trading_state() == "needs_confirmation"
            else "Ready. Read get_market, then trade within the limits."
        ]
        return status

    async def connect(self, *, wait_seconds: float) -> dict[str, Any]:
        """Everything needed to connect, in one call that is safe to repeat.

        Installs the pins, resolves pending requests, reuses a valid key, or opens
        the key page in the user's browser and waits for the downloaded key, which
        it then moves into the private profile and checks with a signed read.
        """
        if not self.config.installation_file.exists():
            self.install_pins()
        installed = profile.load_installation(self.config)
        setup_url = installed.api_url + profile.KEY_SETUP_PATH
        if profile.pending_originals(self.config):
            recovered = await self.recover()
            if profile.pending_originals(self.config):
                return {
                    "status": "pending_request",
                    "recovery": recovered,
                    "say_to_user": "An earlier order's outcome is still being looked up. "
                    "Nothing new is sent until it resolves. I'll check again shortly.",
                }
        try:
            _, grant, path = profile.load_active_key(self.config, installed)
        except SetupError:
            usable = False
        else:
            usable = not profile.key_view(grant, path)["expired"]
        if usable:
            return self._connected(await self.verify(), imported=None)
        now = time.time()
        since = (self._page_opened_at or now) - (
            5 if self._page_opened_at else RECENT_DOWNLOAD_SECONDS
        )
        candidate = profile.fresh_download(self.config, since)
        opened: bool | None = None
        if candidate is None:
            if self._page_opened_at is None or now - self._page_opened_at > REOPEN_PAGE_SECONDS:
                opened = self._browser(setup_url)
                self._page_opened_at = time.time()
            deadline = time.monotonic() + wait_seconds
            while candidate is None and time.monotonic() < deadline:
                await asyncio.sleep(1.0)
                candidate = profile.fresh_download(self.config, self._page_opened_at - 5)
        if candidate is None:
            where = (
                "I opened the FairVenue key page in your browser."
                if opened
                else f"Open the FairVenue key page in your browser:\n{setup_url}\n"
                if opened is False
                else "The FairVenue key page is open in your browser."
            )
            return {
                "status": "waiting_for_key",
                "link": setup_url,
                "browser_opened": opened,
                "say_to_user": f"{where} Sign in with Google if asked, then click Create API key "
                "& download JSON. The button turns on as soon as your account has its 1,000 test "
                "USDC, usually right after sign-in. I pick the key up automatically from your "
                "Downloads folder. Keys from an earlier testnet season no longer work, so create "
                "a new one.",
                "next": "Call connect_account again to keep waiting. If the user saved the file "
                "elsewhere, use find_agent_key_downloads with that folder, then import_agent_key.",
            }
        imported = self.import_key(candidate, remove_source=True)
        self._page_opened_at = None
        return self._connected(await self.verify(), imported=imported)

    def _connected(
        self, verified: dict[str, Any], *, imported: dict[str, Any] | None
    ) -> dict[str, Any]:
        trading = self.trading_state()
        message = f"Connected to the FairVenue TESTNET as {verified['owner']}."
        if imported is not None:
            message += " Your new key is stored privately"
            message += (
                " and the downloaded copy was removed." if "download_removed" in imported else "."
            )
        if trading == "needs_confirmation":
            message += " When I place the first order you'll be asked once to allow trading."
        elif trading == "off":
            message += " Trading is switched off in this server's configuration."
        return {"status": "connected", **verified, "say_to_user": message}

    def install_pins(self) -> dict[str, Any]:
        return profile.install_pins(self.config)

    def find_downloads(self, extra_dirs: list[str]) -> dict[str, Any]:
        found = profile.find_key_downloads(self.config, extra_dirs)
        return {
            "searched": [str(path) for path in self.config.download_dirs] + list(extra_dirs),
            "candidates": found,
            "note": "Only public key fields are shown. The private seed is never returned.",
        }

    def import_key(self, source_path: str, *, remove_source: bool = False) -> dict[str, Any]:
        result = profile.import_key(self.config, source_path, remove_source=remove_source)
        problem = self._journal(
            "agent_key_imported", {"agent_public_key": result.get("agent_public_key")}
        )
        if problem:
            result["journal_error"] = problem
        return result

    async def verify(self) -> dict[str, Any]:
        async with self._lock:
            client = await self._open()
            try:
                snapshot = await client.snapshot()
                pending = client.has_pending_request()
            except AgentError as error:
                raise ToolProblem(
                    "The signed context read failed: "
                    + FAILURE_GUIDANCE.get(error.code, error.code)
                ) from None
            waiting = self._waiting(client)
        context, material = snapshot["context"], snapshot["public_grant"]["material"]
        result = {
            "status": "connected",
            "protocol": "TS2",
            "note": "Signed native context read only. No order was submitted.",
            "owner": material["owner"],
            "agent_public_key": material["agent_public_key"],
            "key_expires_at": utc(int(material["expires_at_ms"])),
            "protocol_time": utc(int(context["protocol_time_ms"])),
            "batch": context["batch_number"],
            "lineage": context["lineage"],
            "fee_mode": "PNL" if context["fee_mode"] == 1 else "FIXED",
            "leverage": context["leverage"],
            "account_settings_enabled": context["account_settings_enabled"],
            "admission": context["admission"],
            "next_command_nonce": snapshot["next_nonce"],
            "pending_request": pending,
            "trading": self.limits_view(),
            "account_visibility": ACCOUNT_VISIBILITY,
        }
        if waiting:
            result["pending_requests"] = [_pending_view(item) for item in waiting]
            result["guidance"] = PENDING_GUIDANCE
        return result

    # Public market data -------------------------------------------------------------

    async def market(self, *, max_age: float = 1.0) -> Market:
        api, settlement, _ = self._public_api()
        try:
            return parse_market(await api.info("native_market", max_age=max_age), settlement)
        except (PublicApiError, MarketDataError) as problem:
            raise ToolProblem(str(problem)) from None

    def _pins_match(self, market: Market) -> bool | None:
        _, _, config_hash = self._public_api()
        return None if config_hash is None else market.config_hash == config_hash

    async def market_snapshot(self, *, depth: int, trades: int) -> dict[str, Any]:
        market = await self.market()
        view = market_view(market, depth=depth, trades=trades)
        matches = self._pins_match(market)
        view["matches_installed_pins"] = matches
        if matches is False:
            view["warning"] = (
                "The served config hash differs from the installed TS2 pins. The deployment may "
                "have changed. Signed requests would be refused. Update the kit before trading."
            )
        return view

    async def wait_for_update(
        self, *, timeout_seconds: float, poll_seconds: float, after_protocol_time_ms: str | None
    ) -> dict[str, Any]:
        target = None
        if after_protocol_time_ms:
            if not _PROTOCOL_TIME.fullmatch(after_protocol_time_ms.strip()):
                raise ToolProblem(
                    "after_protocol_time_ms must be the protocol_time_ms string from get_market."
                )
            target = int(after_protocol_time_ms)
        started = time.monotonic()
        market = await self.market(max_age=0.5)
        baseline = market.fingerprint()
        changed = False
        while True:
            if target is not None:
                changed = market.protocol_time_ms > target
            else:
                changed = market.fingerprint() != baseline
            remaining = timeout_seconds - (time.monotonic() - started)
            if changed or remaining <= 0:
                break
            await asyncio.sleep(min(max(poll_seconds, 2.0), remaining))
            market = await self.market(max_age=0.5)
        return {
            "changed": changed,
            "waited_seconds": round(time.monotonic() - started, 1),
            "market": market_view(market, depth=5, trades=5),
        }

    async def history(self, *, source: str, points: int) -> dict[str, Any]:
        api, settlement, _ = self._public_api()
        kind = "native_book_history" if source == "book" else "native_mark_history"
        try:
            raw = await api.info(kind, max_age=10.0)  # type: ignore[arg-type]
            series = parse_history(raw, source, settlement)
        except (PublicApiError, MarketDataError) as problem:
            raise ToolProblem(str(problem)) from None
        return {
            "source": "native order book midpoints"
            if source == "book"
            else "admitted oracle marks",
            **history_view(series, limit=points),
        }

    # Orders -------------------------------------------------------------------------

    def plan_order(
        self,
        market: Market,
        *,
        side: str,
        quantity: str,
        price: str,
        time_in_force: str,
        post_only: bool,
        reduce_only: bool,
    ) -> OrderPlan:
        side, time_in_force = side.strip().upper(), time_in_force.strip().upper()
        if side not in ("BUY", "SELL"):
            raise ToolProblem("side must be BUY or SELL.")
        if time_in_force not in ("GTC", "IOC"):
            raise ToolProblem(
                "time_in_force must be GTC or IOC. This server sends no market or FOK orders. Use "
                "a price-protected IOC limit order to take liquidity."
            )
        if post_only and time_in_force != "GTC":
            raise ToolProblem("post_only orders must be GTC so they can rest on the book.")
        size, limit = _decimal(quantity, "quantity"), _decimal(price, "price")
        tick, step = market.tick_size, market.step_size
        # TS2 signs whole ticks and steps. Nothing is rounded: off-grid input is refused.
        if size < step:
            raise ToolProblem(f"quantity is below the step size {fmt(step)} BTC.")
        if size % step:
            raise ToolProblem(
                f"quantity {fmt(size)} is not a whole number of {fmt(step)} BTC steps. Use "
                f"{fmt(round_to_step(size, step, up=False))} or "
                f"{fmt(round_to_step(size, step, up=True))}. Nothing was signed."
            )
        if limit % tick:
            raise ToolProblem(
                f"price {fmt(limit)} is not on the {fmt(tick)} tick. Use "
                f"{fmt(round_to_step(limit, tick, up=False))} or "
                f"{fmt(round_to_step(limit, tick, up=True))}. Nothing was signed."
            )
        limits = self.limits
        notional = size * limit
        if size > limits.max_order_quantity:
            raise ToolProblem(
                f"quantity {fmt(size)} BTC exceeds the configured limit "
                f"{fmt(limits.max_order_quantity)} (FAIRVENUE_MAX_ORDER_QUANTITY)."
            )
        if notional > limits.max_order_notional:
            raise ToolProblem(
                f"notional {fmt(notional)} test USDC exceeds the configured limit "
                f"{fmt(limits.max_order_notional)} (FAIRVENUE_MAX_ORDER_NOTIONAL)."
            )
        reference = market.reference_price
        if reference is None:
            raise ToolProblem(
                "No reference price (empty book and no mark). Refusing to price an order."
            )
        deviation = abs(limit - reference) / reference * 10_000
        if deviation > limits.max_price_deviation_bps:
            raise ToolProblem(
                f"price {fmt(limit)} is {fmt(deviation.quantize(Decimal('0.1')))} bps from the "
                f"reference {fmt(reference)}, beyond the configured "
                f"{fmt(limits.max_price_deviation_bps)} bps (FAIRVENUE_MAX_PRICE_DEVIATION_BPS)."
            )
        if post_only and side == "BUY" and market.best_ask and limit >= market.best_ask.price:
            raise ToolProblem(
                f"A post-only BUY at {fmt(limit)} would cross the best ask "
                f"{fmt(market.best_ask.price)}. Post-only orders must rest. Lower the price."
            )
        if post_only and side == "SELL" and market.best_bid and limit <= market.best_bid.price:
            raise ToolProblem(
                f"A post-only SELL at {fmt(limit)} would cross the best bid "
                f"{fmt(market.best_bid.price)}. Post-only orders must rest. Raise the price."
            )
        wire = {
            "symbol": SYMBOL,
            "side": side,
            "order_type": "LIMIT",
            "time_in_force": time_in_force,
            "price": fmt(limit),
            "quantity": fmt(size),
            "reduce_only": reduce_only,
            "post_only": post_only,
        }
        lifetime = GTC_LIFETIME_MS if time_in_force == "GTC" else ONE_SHOT_LIFETIME_MS
        view = {
            **wire,
            "price_ticks": str(int(limit / tick)),
            "quantity_steps": str(int(size / step)),
            "notional_usdc": fmt(notional),
            "reference_price": fmt(reference),
            "deviation_bps": fmt(deviation.quantize(Decimal("0.01"))),
            "fairlag_ms": str(market.fairlag_ms),
            "expires_after": "23 hours unless cancelled or filled"
            if time_in_force == "GTC"
            else "30 seconds",
        }
        return OrderPlan(wire, view, lifetime)

    async def place(self, *, dry_run: bool, **order: Any) -> dict[str, Any]:
        if dry_run:
            plan = self.plan_order(await self.market(), **order)
            return {"status": "dry_run", "signed": False, "order": plan.view}

        async def prepare() -> Prepared:
            # Planned inside the signing lock, against a market read taken just now.
            plan = self.plan_order(await self.market(), **order)
            return (lambda _: {"type": "place", "order": plan.wire}), plan.view, plan.lifetime_ms

        return await self._submit("place_order", "place", prepare)

    async def modify(self, *, order_id: str, dry_run: bool, **order: Any) -> dict[str, Any]:
        """TS2 has no order modify. Nothing is signed, also not for a dry run."""
        raise ToolProblem(MODIFY_UNSUPPORTED)

    async def cancel(self, *, order_id: str) -> dict[str, Any]:
        identifier = _order_id(order_id)

        async def prepare() -> Prepared:
            summary = {"order_id": identifier}
            return (lambda _: {"type": "cancel", "order_id": identifier}), summary, 0

        return await self._submit("cancel_order", "cancel", prepare)

    async def cancel_all(self) -> dict[str, Any]:
        """TS2 kill-all: every resting and FairLag-held order of the account, at once."""

        async def prepare() -> Prepared:
            return (lambda _: {"type": "kill_all"}), {"cancel_all": "kill_all"}, 0

        result = await self._submit("cancel_all_orders", "cancel", prepare)
        status = result.get("status")
        if status == "accepted":
            result["effect"] = (
                "Every resting and FairLag-held order of the account was cancelled. Orders "
                "placed after this command are not affected."
            )
        elif status == "outcome_unknown":
            result["effect"] = "Orders may or may not be cancelled until recovery resolves it."
        else:
            result["effect"] = "No order was cancelled."
        return result

    async def dead_man_switch(self, *, seconds: int, purpose: str) -> dict[str, Any]:
        if seconds:
            seconds = max(seconds, MIN_DMS_DELAY_MS // 1000)
        if seconds * 1000 > MAX_DMS_DELAY_MS:
            raise ToolProblem("The dead man's switch can be armed for at most 86,000 seconds.")
        deadline: dict[str, int] = {}

        async def prepare() -> Prepared:
            def action(now: int) -> dict[str, Any]:
                if not seconds:
                    return {"type": "dead_man_switch", "deadline_ms": "0"}
                deadline["ms"] = now + seconds * 1000
                return {"type": "dead_man_switch", "deadline_ms": str(deadline["ms"])}

            summary = {"dead_man_switch": "disarm"} if not seconds else {"delay_seconds": seconds}
            return action, summary, ONE_SHOT_LIFETIME_MS

        result = await self._submit(purpose, "dead_man_switch", prepare)
        status = result.get("status")
        if status == "accepted" and "ms" in deadline:
            result["deadline"] = utc(deadline["ms"])
            result["effect"] = (
                "Right after the deadline every resting and FairLag-held order of this account "
                "is cancelled and the switch disarms. Arming again replaces the deadline. "
                "Refresh it to keep orders alive."
            )
        elif status == "accepted":
            result["effect"] = "The dead man's switch is disarmed."
        elif status == "outcome_unknown":
            result["effect"] = "The switch may or may not be armed until recovery resolves it."
        else:
            result["effect"] = "No switch change was made."
        return result

    async def leverage(self, *, leverage: int) -> dict[str, Any]:
        if not 1 <= leverage <= MAX_LEVERAGE:
            raise ToolProblem(f"leverage must be a whole number from 1 to {MAX_LEVERAGE}.")

        async def prepare() -> Prepared:
            action = {"type": "leverage", "leverage": str(leverage)}
            return (lambda _: action), {"leverage": str(leverage)}, ONE_SHOT_LIFETIME_MS

        return await self._submit("set_leverage", "place", prepare)

    async def fee_mode(self, *, mode: str) -> dict[str, Any]:
        mode = mode.strip().upper()
        if mode not in ("FIXED", "PNL"):
            raise ToolProblem("mode must be FIXED or PNL.")

        async def prepare() -> Prepared:
            action = {"type": "fee_mode", "mode": mode}
            return (lambda _: action), {"mode": mode}, ONE_SHOT_LIFETIME_MS

        return await self._submit("set_fee_mode", "place", prepare)

    async def _submit(
        self, kind: str, permission: str, prepare: Callable[[], Awaitable[Prepared]]
    ) -> dict[str, Any]:
        self._require_trading()
        async with self._lock:
            client = await self._open()
            waiting = self._waiting(client)
            if waiting:
                return {
                    "status": "blocked",
                    "reason": "pending_request",
                    "pending_requests": [_pending_view(item) for item in waiting],
                    "guidance": PENDING_GUIDANCE,
                }
            material = (self._grant or {}).get("material") or {}
            if not int(material.get("permissions") or 0) & PERMISSION[permission]:
                raise ToolProblem(
                    f"This key lacks the {permission.replace('_', ' ')} permission. Create a new "
                    "API key in the Arena terminal."
                )
            make_action, summary, lifetime = await prepare()
            try:
                snapshot = await client.snapshot()
            except AgentError as error:
                return self._record(kind, summary, self._refusal(client, error.code))
            now = _now_ms(snapshot["context"])
            key_expiry = int(snapshot["public_grant"]["material"]["expires_at_ms"])
            expiry = min(now + (lifetime or ONE_SHOT_LIFETIME_MS), key_expiry)
            if expiry <= now + 1_000:
                raise ToolProblem(
                    "This key has expired. Create a new API key in the Arena terminal."
                )
            request = {
                "nonce": snapshot["next_nonce"],
                "expiresAtMs": str(expiry),
                "action": make_action(now),
            }
            # Written before signing: if the journal is broken, nothing is signed, and a
            # cancelled or crashed call still leaves a record of what was being sent.
            problem = self._journal("submitting", {"for": kind, "request": summary})
            if problem:
                raise ToolProblem(problem + " Nothing was signed.")
            try:
                result = await client.run(request, timeout_ms=60_000)
            except AgentError as error:
                outcome = self._refusal(client, error.code)
            else:
                outcome = self._executed(result)
        return self._record(kind, summary, outcome)

    def _record(
        self, kind: str, summary: dict[str, Any], outcome: dict[str, Any]
    ) -> dict[str, Any]:
        """The outcome is returned even when the journal cannot be written."""
        result = {**outcome, "request": summary}
        problem = self._journal(kind, {"request": summary, **outcome})
        if problem:
            result["journal_error"] = problem
        return result

    @staticmethod
    def _executed(result: dict[str, Any]) -> dict[str, Any]:
        ack, recovery = result["ack"], result["recovery"]
        status = recovery["status"]
        outcome: dict[str, Any] = {
            "status": {"accepted": "accepted", "rejected": "rejected"}.get(status, "not_executed"),
            "action": ack["action"],
            "duplicate": bool(ack.get("duplicate", False)),
        }
        for key in ("order_id", "held", "batch", "lineage"):
            if key in ack:
                outcome[key] = ack[key]
        if status == "accepted":
            outcome["meaning"] = (
                "The exchange journaled this command in soft, operator-trusted state. An order "
                "then waits for FairLag and may rest, fill or partly fill. Keep its order_id to "
                "cancel it. " + ACCOUNT_VISIBILITY
            )
        elif status == "rejected":
            outcome["meaning"] = "The exchange refused this command. Nothing executed."
        elif status == "not_placed":
            outcome["meaning"] = "It never reached the exchange journal and can never execute."
        else:
            outcome["meaning"] = "Its batch was voided. It never takes effect."
        return outcome

    @staticmethod
    def _refusal(client: Any, code: str) -> dict[str, Any]:
        if code in ts2.NOT_PLACED:
            return {
                "status": "not_placed",
                "reason": code,
                "retry": ts2.NOT_PLACED[code],
                "guidance": NOT_PLACED_GUIDANCE[code],
            }
        if code == "outcome_unknown":
            try:
                pending = client.has_pending_request()
            except AgentError:
                pending = True
            if not pending:
                # The SDK already resolved it from the original's exact lookup.
                return {
                    "status": "not_executed",
                    "guidance": "Its exact lookup resolved it without effect. Nothing was "
                    "placed. Submit a new request if it is still wanted.",
                }
            return {
                "status": "outcome_unknown",
                "guidance": "The request may or may not have executed. Its exact original is "
                "kept locally. Call recover_pending_request now or after a restart. Do not send "
                "another order until it resolves.",
            }
        return {
            "status": "failed",
            "reason": code,
            "sent_for_execution": False,
            "guidance": FAILURE_GUIDANCE.get(code, "The request failed before admission.")
            + " Nothing was placed.",
        }

    async def recover(self) -> dict[str, Any]:
        """Resolve one pending original, whichever key holds it (expired keys included)."""
        async with self._lock:
            installed = profile.load_installation(self.config)
            waiting = profile.pending_originals(self.config)
            if not waiting:
                return {"status": "nothing_pending", "guidance": "No signed request is waiting."}
            target = waiting[0]
            summary = _pending_view(target)
            if target.get("unreadable"):
                return self._record(
                    "recover_pending_request",
                    summary,
                    {
                        "status": "still_unknown",
                        "reason": "unreadable",
                        "guidance": f"The continuity database {target.get('custody')} cannot be "
                        "read. Inspect it and never delete it. Signing stays blocked.",
                    },
                )
            key_path = profile.key_for_namespace(self.config, target["namespace"])
            if key_path is None:
                return {
                    "status": "still_unknown",
                    "reason": "key_missing",
                    "request": summary,
                    "guidance": f"The key {target['agent_public_key']} that signed this request "
                    "is not in the profile. Import that key file again (an expired key is "
                    "accepted for recovery), then call recover_pending_request.",
                }
            signer, grant = profile.load_key(installed, key_path)
            client = self._open_key(installed, signer, grant, key_path)
            try:
                result = await client.recover_original()
            except AgentError as error:
                outcome = {
                    "status": "still_unknown",
                    "reason": error.code,
                    "guidance": "The lookup did not complete. The original stays retained. Try "
                    "again later. Never resend, reprice or resign it.",
                }
                return self._record("recover_pending_request", summary, outcome)
            status = result["status"]
            outcome = {
                "status": status,
                "resolved": status in ts2.TERMINAL,
                "meaning": RECOVERY_MEANINGS.get(
                    status, "Unexpected status. The original stays retained."
                ),
            }
            remaining = len(waiting) - (1 if outcome["resolved"] else 0)
            if remaining:
                outcome["other_pending_requests"] = remaining
        return self._record("recover_pending_request", summary, outcome)
