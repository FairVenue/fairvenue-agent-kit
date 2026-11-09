"""Offline tests for the optional MCP server: synthetic keys, pins and server only.

They run the real setup, continuity and signing paths against the package's
synthetic TS2 fixture server. No hosted, funded, proof or live acceptance.
"""

from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import os
import shutil
import tempfile
import time
import unittest
from decimal import Decimal
from pathlib import Path
from typing import Any
from unittest.mock import patch

from fairvenue_mcp import profile as profile_module
from fairvenue_mcp import trading as trading_module
from fairvenue_mcp.config import ConfigError, Limits, load_config
from fairvenue_mcp.journal import Journal
from fairvenue_mcp.market import MarketDataError, parse_market
from fairvenue_mcp.profile import SetupError
from fairvenue_mcp.trading import ToolProblem, Trader
from fairvenue_schema6 import NativeTestnetClient, ts2

from test_native_testnet import API, SEED, NativeFixture, market, not_placed

ORDER_ID = "0x" + "ab" * 32
ORDER = {
    "side": "BUY",
    "quantity": "0.001",
    "price": "79990",
    "time_in_force": "GTC",
    "post_only": False,
    "reduce_only": False,
}


def pins_document(**changes: Any) -> dict[str, Any]:
    return {
        "schema": "fairvenue-ts2-testnet-installation-v1",
        "environment": "TESTNET",
        **ts2.PINS,
        "api_namespace": "/v1",
        "api_url": API,
        **changes,
    }


class FakePublic:
    """Public reads of the same synthetic market the SDK reads through the fixture."""

    def __init__(self, fixture: NativeFixture) -> None:
        self.fixture = fixture
        self.calls = 0

    async def info(self, kind: str, *, max_age: float = 0.0) -> dict[str, Any]:
        self.calls += 1
        if kind != "native_market":
            raise AssertionError(kind)
        return copy.deepcopy(self.fixture.market)


class Profile:
    """A private FAIRVENUE_HOME with synthetic pins and one downloaded key."""

    def __init__(self, fixture: NativeFixture, *, trading: str = "on") -> None:
        self.fixture = fixture
        self.opened: list[str] = []  # URLs the server asked the browser to open.
        self.root = Path(tempfile.mkdtemp(prefix="fairvenue-mcp-")).resolve()
        self.home = self.root / "home"
        self.home.mkdir(mode=0o700)
        self.downloads = self.root / "Downloads"
        self.downloads.mkdir()
        data = json.dumps(pins_document()).encode()
        self.pins = self.home / "installation.json"
        self.pins.write_bytes(data)
        self.pins.chmod(0o600)
        self.download = self.write_key()
        self.public = FakePublic(fixture)
        self.config = load_config(
            {
                "FAIRVENUE_HOME": str(self.home),
                "FAIRVENUE_INSTALLATION_SHA256": hashlib.sha256(data).hexdigest(),
                "FAIRVENUE_TRADING": trading,
                "FAIRVENUE_DOWNLOAD_DIRS": str(self.downloads),
            }
        )

    def write_key(self, name: str = "fairvenue-TESTNET-agent.json", **changes: Any) -> Path:
        path = self.downloads / name
        path.write_text(json.dumps(self.fixture.export(**changes)))
        path.chmod(0o644)  # Browsers save downloads world-readable; import fixes the copy.
        return path

    def trader(self, browser: Any = None, **overrides: Any) -> Trader:
        fixture = self.fixture

        def no_browser(url: str) -> bool:
            # Tests never launch a real browser: record the request instead.
            self.opened.append(url)
            return False

        def opener(
            *,
            policy: Any,
            grant: Any,
            signer: Any,
            api_url: str,
            custody_path: str,
            initialize: bool,
        ) -> Any:
            return NativeTestnetClient(
                policy=policy,
                grant=grant,
                signer=signer,
                transport=fixture,
                approved_api_url=api_url,
                custody_path=custody_path,
                initialize=initialize,
            )

        config = self.config
        if overrides:
            config = type(config)(**{**config.__dict__, **overrides})
        return Trader(
            config,
            opener=opener,
            public_factory=lambda *_: self.public,  # type: ignore[arg-type,return-value]
            browser=browser or no_browser,
        )

    def write_other_key(self, name: str = "fairvenue-TESTNET-agent (1).json") -> Path:
        """A second, separately delegated synthetic key for the same deployment."""
        other = NativeFixture(seed="0x" + "4d" * 32, nonce="2")  # Public synthetic seed.
        return self.write_key(name, delegation=other.grant, private_key_seed=other.seed)

    def cleanup(self) -> None:
        shutil.rmtree(self.root)


class McpTraderTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.fixture = NativeFixture()
        self.profile = Profile(self.fixture)
        self.addCleanup(self.profile.cleanup)

    async def ready(self, **overrides: Any) -> Trader:
        trader = self.profile.trader(**overrides)
        self.addCleanup(trader.close)
        trader.import_key(str(self.profile.download))
        await trader.verify()
        return trader

    async def test_guided_setup_imports_key_privately_and_verifies_without_orders(self) -> None:
        trader = self.profile.trader()
        self.addCleanup(trader.close)
        status = await trader.setup_status()
        self.assertEqual(status["protocol"], "TS2")
        self.assertFalse(status["agent_key"]["ready"])
        self.assertIn("connect_account", status["next_steps"][0])
        found = trader.find_downloads([])["candidates"]
        self.assertEqual(len(found), 1)
        self.assertTrue(found[0]["matches_installed_deployment"])
        self.assertEqual(found[0]["config_hash"], ts2.CONFIG_HASH)
        imported = trader.import_key(str(self.profile.download))
        self.assertEqual(imported["status"], "imported")
        copy_path = Path(imported["file"])
        self.assertEqual(copy_path.stat().st_mode & 0o777, 0o600)
        self.assertTrue(self.profile.download.exists())
        verified = await trader.verify()
        self.assertEqual(verified["status"], "connected")
        self.assertEqual(verified["owner"], self.fixture.grant["material"]["owner"])
        self.assertEqual((verified["fee_mode"], verified["leverage"]), ("FIXED", "10"))
        self.assertFalse(verified["pending_request"])
        self.assertEqual(self.fixture.calls["admit"], 0)
        status = await trader.setup_status()
        self.assertEqual(status["deployment"]["config_hash"], ts2.CONFIG_HASH)
        self.assertTrue(status["continuity"]["initialized"])
        self.assertFalse(status["continuity"]["pending_request"])
        # Nothing returned or journaled contains the seed or the internal account word.
        everything = json.dumps([status, found, imported, verified, trader.journal.tail(50)])
        for secret in (SEED[2:], "private_key_seed", self.fixture.grant["material"]["account_key"]):
            self.assertNotIn(secret, everything)
        self.assertNotIn(SEED[2:], self.profile.config.journal_file.read_text())

    async def test_import_refuses_v4_other_deployments_v0_and_expired_keys(self) -> None:
        trader = self.profile.trader()
        self.addCleanup(trader.close)
        v4 = self.profile.write_key("fairvenue-v4.json", schema=ts2.V4_KEY_SCHEMA)
        with self.assertRaisesRegex(SetupError, "TS2: create a new bot key"):
            trader.import_key(str(v4))
        foreign = self.profile.write_key("fairvenue-old.json", settlement="0x" + "1" * 40)
        with self.assertRaisesRegex(SetupError, "previous testnet deployment"):
            trader.import_key(str(foreign))
        legacy = self.profile.write_key("fairvenue-v0.json", schema="fairvenue-testnet-v0")
        with self.assertRaisesRegex(SetupError, "not a TS2 bot key"):
            trader.import_key(str(legacy))
        grant = copy.deepcopy(self.fixture.grant)
        grant["material"].update(issued_at_ms="0", expires_at_ms="40000", max_expiry_ms="40000")
        expired = self.profile.write_key("fairvenue-expired.json", delegation=grant)
        with self.assertRaisesRegex(SetupError, "expired"):
            trader.import_key(str(expired))
        self.assertFalse(
            self.profile.config.keys_dir.exists() and any(self.profile.config.keys_dir.iterdir())
        )
        # Only TS2 files are candidates. The V4 file is never picked up.
        kinds = {item["kind"] for item in trader.find_downloads([])["candidates"]}
        self.assertEqual(kinds, {"native_testnet"})

    async def test_place_order_signs_whole_ticks_and_steps_once_and_returns_its_id(self) -> None:
        trader = await self.ready()
        result = await trader.place(
            dry_run=False,
            side="buy",
            quantity="0.002",
            price="79950.0",
            time_in_force="GTC",
            post_only=True,
            reduce_only=False,
        )
        self.assertEqual(result["status"], "accepted", result)
        request = result["request"]
        self.assertEqual((request["quantity"], request["price"]), ("0.002", "79950"))
        self.assertEqual((request["price_ticks"], request["quantity_steps"]), ("799500", "2"))
        self.assertEqual(self.fixture.calls["admit"], 1)
        signed = self.fixture.admitted()[0]
        action = signed["action"]
        self.assertEqual(
            (action["side"], action["limit_price_ticks"], action["quantity_lots"]),
            (0, "799500", "2"),
        )
        self.assertEqual(
            (action["kind"], action["time_in_force"], action["post_only"]), (0, 0, True)
        )
        # A GTC order rests for 23 hours, never past the key.
        lifetime = int(action["expires_at_ms"]) - int(time.time() * 1000)
        self.assertGreater(lifetime, 22 * 3_600_000)
        _, order_id = ts2.signing_message(
            ts2.PINS,
            signed["account_key"],
            self.fixture.signer.public_key,
            action,
            self.fixture.crypto,
        )
        self.assertEqual(result["order_id"], order_id)
        journal = trader.journal.tail(5)
        self.assertEqual(journal[-1]["kind"], "place_order")
        self.assertEqual((journal[-1]["status"], journal[-1]["order_id"]), ("accepted", order_id))
        self.assertNotIn("signature", json.dumps(journal))
        # That order ID cancels it.
        cancelled = await trader.cancel(order_id=" " + order_id.upper().replace("0X", "0x"))
        self.assertEqual(cancelled["status"], "accepted")
        self.assertEqual(
            self.fixture.admitted()[1]["action"], {"type": "cancel", "order_id": order_id}
        )

    async def test_off_tick_and_off_step_input_is_refused_never_rounded(self) -> None:
        trader = await self.ready()
        for change, message in (
            ({"price": "79950.07"}, "not on the 0.1 tick. Use 79950 or 79950.1"),
            ({"quantity": "0.0019"}, "not a whole number of 0.001 BTC steps. Use 0.001 or 0.002"),
        ):
            with self.subTest(change=change), self.assertRaisesRegex(ToolProblem, message):
                await trader.place(dry_run=True, **{**ORDER, **change})
            with self.assertRaisesRegex(ToolProblem, "Nothing was signed"):
                await trader.place(dry_run=False, **{**ORDER, **change})
        self.assertEqual(self.fixture.calls["admit"], 0)

    async def test_trading_off_refuses_before_any_signature_but_dry_run_works(self) -> None:
        trader = await self.ready(trading_mode="off")
        signed = len(self.fixture.signed)
        with self.assertRaisesRegex(ToolProblem, "FAIRVENUE_TRADING=off"):
            await trader.place(dry_run=False, **ORDER)
        with self.assertRaisesRegex(ToolProblem, "FAIRVENUE_TRADING=off"):
            await trader.dead_man_switch(seconds=0, purpose="set_dead_man_switch")
        # In ask mode nothing is signed until the user confirms in the dialog.
        asking = self.profile.trader(trading_mode="ask")
        self.addCleanup(asking.close)
        with self.assertRaisesRegex(ToolProblem, "cannot show the confirmation dialog"):
            await asking.cancel(order_id=ORDER_ID)
        preview = await trader.place(
            dry_run=True,
            side="SELL",
            quantity="0.002",
            price="80000.3",
            time_in_force="IOC",
            post_only=False,
            reduce_only=True,
        )
        self.assertEqual(preview["status"], "dry_run")
        self.assertEqual(preview["order"]["price"], "80000.3")
        self.assertEqual(preview["order"]["expires_after"], "30 seconds")
        self.assertEqual(len(self.fixture.signed), signed)
        self.assertEqual(self.fixture.calls["admit"], 0)

    async def test_local_guards_refuse_unprotected_or_oversized_orders(self) -> None:
        trader = await self.ready(
            limits=Limits(
                max_order_quantity=Decimal("0.05"),
                max_order_notional=Decimal("1000"),
                max_price_deviation_bps=Decimal("100"),
            )
        )
        cases = [
            ({"quantity": "0.06"}, "FAIRVENUE_MAX_ORDER_QUANTITY"),
            ({"quantity": "0.02"}, "FAIRVENUE_MAX_ORDER_NOTIONAL"),
            ({"price": "81000"}, "bps from the reference"),
            ({"quantity": "0.0004"}, "below the step size"),
            ({"price": "80000.2", "post_only": True}, "would cross the best ask"),
            ({"time_in_force": "IOC", "post_only": True}, "must be GTC"),
            ({"time_in_force": "FOK"}, "no market or FOK"),
            ({"quantity": "1e-3"}, "plain decimal"),
            ({"price": "abc"}, "decimal string"),
        ]
        for change, message in cases:
            with self.subTest(change=change), self.assertRaisesRegex(ToolProblem, message):
                await trader.place(dry_run=False, **{**ORDER, **change})
        with self.assertRaisesRegex(ToolProblem, "Order changes are not available"):
            await trader.modify(order_id=ORDER_ID, dry_run=True, **ORDER)
        with self.assertRaisesRegex(ToolProblem, "0x order ID"):
            await trader.cancel(order_id="17")
        with self.assertRaisesRegex(ToolProblem, "from 1 to 100"):
            await trader.leverage(leverage=101)
        self.assertEqual(self.fixture.calls["admit"], 0)

    async def test_lost_reply_blocks_until_exact_original_recovery(self) -> None:
        trader = await self.ready()
        self.fixture.lost_reply = True
        unknown = await trader.place(dry_run=False, **ORDER)
        self.assertEqual(unknown["status"], "outcome_unknown")
        blocked = await trader.place(dry_run=False, **ORDER)
        self.assertEqual(blocked["status"], "blocked")
        self.assertEqual(blocked["pending_requests"][0]["action"]["type"], "order")
        self.assertEqual(self.fixture.calls["admit"], 1)
        # A restarted server sees the same retained original.
        trader.close()
        restarted = self.profile.trader()
        self.addCleanup(restarted.close)
        self.assertTrue((await restarted.setup_status())["continuity"]["pending_request"])
        recovered = await restarted.recover()
        self.assertEqual((recovered["status"], recovered["resolved"]), ("accepted", True))
        self.assertEqual(self.fixture.calls["admit"], 1)  # Recovery never resends.
        self.assertEqual((await restarted.recover())["status"], "nothing_pending")

    async def test_definitive_refusals_and_terminal_outcomes_need_no_recovery(self) -> None:
        trader = await self.ready()
        order = {**ORDER, "side": "SELL", "price": "80001", "post_only": True}
        self.fixture.refusal = lambda request: (409, not_placed(request, "rejected"))
        refused = await trader.place(dry_run=False, **order)
        self.assertEqual((refused["status"], refused["reason"]), ("not_placed", "rejected"))
        self.assertIn("Nothing was placed", refused["guidance"])
        self.fixture.refusal = None
        self.fixture.recovery_status = "not_settled"
        terminal = await trader.place(dry_run=False, **order)
        self.assertEqual(terminal["status"], "not_executed")
        self.fixture.recovery_status = "not_placed"
        rejected = await trader.place(dry_run=False, **order)
        self.assertEqual(rejected["status"], "rejected")
        self.assertFalse((await trader.setup_status())["continuity"]["pending_request"])

    async def test_cancel_all_is_an_immediate_kill_all(self) -> None:
        trader = await self.ready()
        result = await trader.cancel_all()
        self.assertEqual(result["status"], "accepted")
        self.assertIn("was cancelled", result["effect"])
        action = self.fixture.admitted()[-1]["action"]
        self.assertEqual(action["type"], "kill_all")
        self.assertEqual(set(action), {"type", "client_nonce"})

    async def test_account_commands_use_the_security_command_nonce(self) -> None:
        trader = await self.ready()
        armed = await trader.dead_man_switch(seconds=2, purpose="set_dead_man_switch")
        self.assertEqual(armed["status"], "accepted")
        self.assertIn("Right after the deadline", armed["effect"])
        self.assertEqual((await trader.leverage(leverage=100))["status"], "accepted")
        self.assertEqual((await trader.fee_mode(mode="pnl"))["status"], "accepted")
        dms, leverage, fee = (request["action"] for request in self.fixture.admitted())
        self.assertEqual((dms["type"], dms["command_nonce"]), ("set_dms", "1"))
        self.assertGreaterEqual(
            int(dms["deadline_ms"]), int(self.fixture.context["protocol_time_ms"]) + 2000
        )
        self.assertEqual((leverage["leverage"], leverage["command_nonce"]), ("100", "2"))
        self.assertEqual(
            (fee["type"], fee["fee_mode"], fee["command_nonce"]), ("set_fee_mode", 1, "3")
        )
        # The testnet can keep account settings closed: nothing is signed then.
        self.fixture.context["account_settings_enabled"] = False
        closed = await trader.leverage(leverage=5)
        self.assertEqual((closed["status"], closed["reason"]), ("not_placed", "admission_closed"))
        self.assertEqual(self.fixture.calls["admit"], 3)

    async def test_missing_established_continuity_is_never_reinitialized(self) -> None:
        trader = await self.ready()
        custody = Path((await trader.setup_status())["continuity"]["path"])
        trader.close()
        for path in custody.parent.iterdir():
            path.unlink()
        restarted = self.profile.trader()
        self.addCleanup(restarted.close)
        with self.assertRaisesRegex(SetupError, "never reinitialize"):
            await restarted.verify()
        self.assertFalse(custody.exists())

    async def test_a_retired_v4_profile_is_named_and_never_used(self) -> None:
        legacy = self.profile.root / "legacy"
        legacy.mkdir()
        trader = self.profile.trader()
        self.addCleanup(trader.close)
        with patch.object(trading_module, "LEGACY_V4_HOME", str(legacy)):
            status = await trader.setup_status()
        self.assertIn("V4", status["previous_testnet_profile"]["note"])
        old = self.profile.root / "old-home"
        old.mkdir(mode=0o700)
        pins = old / "installation.json"
        pins.write_text(json.dumps({"schema": profile_module.V4_INSTALLATION_SCHEMA}))
        pins.chmod(0o600)
        with self.assertRaisesRegex(SetupError, "retired V4 testnet"):
            profile_module.load_installation(load_config({"FAIRVENUE_HOME": str(old)}))
        self.assertTrue(pins.exists())


class McpReviewRegressionTests(unittest.IsolatedAsyncioTestCase):
    """Each test pins one finding of the independent review."""

    def setUp(self) -> None:
        self.fixture = NativeFixture()
        self.profile = Profile(self.fixture)
        self.addCleanup(self.profile.cleanup)

    async def ready(self) -> Trader:
        trader = self.profile.trader()
        self.addCleanup(trader.close)
        trader.import_key(str(self.profile.download))
        await trader.verify()
        return trader

    async def test_switching_keys_cannot_bypass_an_unknown_outcome(self) -> None:
        trader = await self.ready()
        self.fixture.lost_reply = True
        self.assertEqual((await trader.place(dry_run=False, **ORDER))["status"], "outcome_unknown")
        other = self.profile.write_other_key()
        with self.assertRaisesRegex(SetupError, "recover_pending_request before switching"):
            trader.import_key(str(other))
        status = await trader.setup_status()
        self.assertIn("recover_pending_request", status["next_steps"][0])
        pending = status["pending_requests"][0]
        self.assertEqual(pending["action"]["type"], "order")
        self.assertNotIn("signature", json.dumps(pending))
        # Even when the original key looks expired, it can come back for recovery only.
        with patch.object(profile_module.time, "time", return_value=10**13):
            again = trader.import_key(str(self.profile.download))
            self.assertIn("recover", again.get("note", ""))
        recovered = await trader.recover()
        self.assertEqual(recovered["status"], "accepted")
        self.assertEqual(recovered["request"]["action"]["type"], "order")
        self.assertEqual(trader.import_key(str(other))["status"], "imported")
        self.assertEqual(self.fixture.calls["admit"], 1)

    async def test_signing_is_blocked_while_any_namespace_is_pending(self) -> None:
        trader = await self.ready()
        self.fixture.lost_reply = True
        await trader.place(dry_run=False, **ORDER)
        # A second server process (or a restart) also refuses to sign anything new.
        trader.close()
        restarted = self.profile.trader()
        self.addCleanup(restarted.close)
        blocked = await restarted.place(dry_run=False, **ORDER)
        self.assertEqual(blocked["status"], "blocked")
        self.assertEqual(blocked["pending_requests"][0]["action"]["type"], "order")
        self.assertEqual(self.fixture.calls["admit"], 1)

    async def test_a_broken_journal_never_hides_an_executed_order(self) -> None:
        trader = await self.ready()
        journal = self.profile.config.journal_file
        journal.chmod(0o400)
        with self.assertRaisesRegex(ToolProblem, "Nothing was signed"):
            await trader.place(dry_run=False, **ORDER)
        self.assertEqual(self.fixture.calls["admit"], 0)
        journal.chmod(0o600)
        original = Journal.append

        def flaky(journal: Journal, kind: str, entry: dict[str, Any]) -> None:
            if kind == "place_order":
                raise OSError(28, "No space left on device")
            original(journal, kind, entry)

        with patch.object(Journal, "append", flaky):
            result = await trader.place(dry_run=False, **ORDER)
        self.assertEqual(result["status"], "accepted")
        self.assertIn("could not be written", result["journal_error"])
        self.assertEqual(self.fixture.calls["admit"], 1)
        submitted = [entry for entry in trader.journal.tail(10) if entry["kind"] == "submitting"]
        self.assertEqual(submitted[-1]["for"], "place_order")

    async def test_first_run_with_external_paths_creates_a_private_home(self) -> None:
        fixture = self.fixture
        elsewhere = self.profile.root / "elsewhere"
        elsewhere.mkdir()
        pins = elsewhere / "pins.json"
        shutil.copy(self.profile.pins, pins)
        pins.chmod(0o600)
        key = elsewhere / "agent.json"
        shutil.copy(self.profile.download, key)
        key.chmod(0o600)
        home = self.profile.root / "fresh" / "profile"
        config = load_config(
            {
                "FAIRVENUE_HOME": str(home),
                "FAIRVENUE_INSTALLATION_FILE": str(pins),
                "FAIRVENUE_INSTALLATION_SHA256": self.profile.config.installation_sha256,
                "FAIRVENUE_KEY_FILE": str(key),
            }
        )
        old = os.umask(0o022)
        try:
            trader = Trader(
                config,
                opener=lambda **kwargs: NativeTestnetClient(transport=fixture, **_rename(kwargs)),
                public_factory=lambda *_: self.profile.public,  # type: ignore[arg-type,return-value]
            )
            self.addCleanup(trader.close)
            self.assertEqual((await trader.verify())["status"], "connected")
        finally:
            os.umask(old)
        self.assertEqual(home.stat().st_mode & 0o777, 0o700)
        self.assertEqual(home.parent.stat().st_mode & 0o777, 0o700)
        state = json.loads(config.state_file.read_text())
        self.assertEqual(len(state["continuity"]), 1)

    async def test_switch_results_only_claim_effects_that_happened(self) -> None:
        trader = await self.ready()
        self.fixture.refusal = lambda request: (429, not_placed(request, "rate_limited"))
        refused = await trader.dead_man_switch(seconds=2, purpose="set_dead_man_switch")
        self.assertEqual(refused["status"], "not_placed")
        self.assertEqual(refused["effect"], "No switch change was made.")
        self.assertNotIn("deadline", refused)
        cancel_all = await trader.cancel_all()
        self.assertEqual(cancel_all["effect"], "No order was cancelled.")

    async def test_odd_downloads_crafted_names_and_bad_state_fail_safely(self) -> None:
        trader = self.profile.trader()
        self.addCleanup(trader.close)
        odd = copy.deepcopy(self.fixture.grant)
        odd["material"]["issued_at_ms"] = "9999999999999999"
        self.profile.write_key("fairvenue-odd.json", delegation=odd)
        paths = [item["path"] for item in trader.find_downloads([])["candidates"]]
        self.assertIn(str(self.profile.download), paths)
        crafted = copy.deepcopy(self.fixture.grant)
        crafted["material"]["agent_public_key"] = "0x../../escape-" + "a" * 49
        evil = self.profile.write_key("fairvenue-evil.json", delegation=crafted)
        with self.assertRaises(SetupError):
            trader.import_key(str(evil))
        self.assertFalse(any(self.profile.root.glob("escape*")))
        self.assertFalse(any(self.profile.home.glob("escape*")))
        self.profile.config.state_file.write_text('{"schema": "something-else"}')
        with self.assertRaisesRegex(SetupError, "Never delete it"):
            trader.import_key(str(self.profile.download))
        with self.assertRaisesRegex(ToolProblem, "protocol_time_ms"):
            await trader.wait_for_update(
                timeout_seconds=1, poll_seconds=1, after_protocol_time_ms="latest"
            )


def _rename(kwargs: dict[str, Any]) -> dict[str, Any]:
    """The opener's keyword names, mapped onto the SDK constructor."""
    api_url = kwargs.pop("api_url")
    return {**kwargs, "approved_api_url": api_url}


class McpConnectTests(unittest.IsolatedAsyncioTestCase):
    """connect_account: the user only clicks in the browser."""

    def setUp(self) -> None:
        self.fixture = NativeFixture()
        self.profile = Profile(self.fixture)
        self.addCleanup(self.profile.cleanup)

    async def test_opens_the_page_and_picks_up_the_download_by_itself(self) -> None:
        self.profile.download.unlink()  # Nothing downloaded yet.
        loop = asyncio.get_running_loop()

        def browser(url: str) -> bool:
            self.profile.opened.append(url)
            # The user signs in and clicks Create API key & download JSON a moment later.
            loop.call_later(1.5, self.profile.write_key)
            return True

        trader = self.profile.trader(browser=browser)
        self.addCleanup(trader.close)
        result = await trader.connect(wait_seconds=10)
        self.assertEqual(result["status"], "connected")
        self.assertEqual(self.profile.opened, [API + "?setup=api-key#native-agent-key"])
        self.assertIn("downloaded copy was removed", result["say_to_user"])
        self.assertFalse(self.profile.download.exists())  # Moved, never left in Downloads.
        stored = list(self.profile.config.keys_dir.glob("*.json"))
        self.assertEqual(len(stored), 1)
        self.assertEqual(stored[0].stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.fixture.calls["admit"], 0)
        # Connected again later: nothing to open, nothing to ask.
        again = await trader.connect(wait_seconds=5)
        self.assertEqual(again["status"], "connected")
        self.assertEqual(len(self.profile.opened), 1)

    async def test_waits_politely_and_never_reopens_the_page_in_a_loop(self) -> None:
        self.profile.download.unlink()
        self.profile.write_key("fairvenue-old-v4.json", schema=ts2.V4_KEY_SCHEMA)
        trader = self.profile.trader()  # A browser that could not be opened (headless).
        self.addCleanup(trader.close)
        first = await trader.connect(wait_seconds=1)
        self.assertEqual(first["status"], "waiting_for_key")  # A V4 download is never used.
        self.assertIn(first["link"], first["say_to_user"])
        self.assertIn("Create API key & download JSON", first["say_to_user"])
        self.assertIn("create a new one", first["say_to_user"])
        second = await trader.connect(wait_seconds=1)
        self.assertEqual(second["status"], "waiting_for_key")
        self.assertEqual(len(self.profile.opened), 1)
        self.profile.write_key()
        third = await trader.connect(wait_seconds=5)
        self.assertEqual(third["status"], "connected")

    async def test_a_key_downloaded_just_before_is_used_without_opening_anything(self) -> None:
        trader = self.profile.trader()
        self.addCleanup(trader.close)
        result = await trader.connect(wait_seconds=5)
        self.assertEqual(result["status"], "connected")
        self.assertEqual(self.profile.opened, [])
        self.assertEqual(result["owner"], self.fixture.grant["material"]["owner"])


TOOLS = {
    "connect_account",
    "setup_status",
    "enable_trading",
    "disable_trading",
    "install_deployment_pins",
    "find_agent_key_downloads",
    "import_agent_key",
    "verify_connection",
    "get_market",
    "get_price_history",
    "wait_for_market_update",
    "place_order",
    "modify_order",
    "cancel_order",
    "cancel_all_orders",
    "set_dead_man_switch",
    "set_leverage",
    "set_fee_mode",
    "recover_pending_request",
    "get_journal",
    "get_guide",
}


class McpProtocolTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.fixture = NativeFixture()
        self.order = {"side": "BUY", "quantity": "0.001", "price": "79990"}

    def trader(self, trading: str) -> Trader:
        profile = Profile(self.fixture, trading=trading)
        self.addCleanup(profile.cleanup)
        trader = profile.trader()
        self.addCleanup(trader.close)
        return trader

    async def test_tools_prompts_and_errors_over_mcp(self) -> None:
        from mcp import Client

        from fairvenue_mcp.server import create_server

        trader = self.trader("off")
        async with asyncio.timeout(60), Client(create_server(trader)) as client:
            tools = {tool.name: tool for tool in (await client.list_tools()).tools}
            self.assertEqual(set(tools), TOOLS)  # The same tools as before TS2.

            def arguments(name: str) -> set[str]:
                return set(tools[name].input_schema["properties"])

            # Tool arguments are unchanged. The dialog answer is never a model argument.
            self.assertEqual(
                arguments("place_order"),
                {
                    "side",
                    "quantity",
                    "price",
                    "time_in_force",
                    "post_only",
                    "reduce_only",
                    "dry_run",
                },
            )
            self.assertEqual(arguments("modify_order"), arguments("place_order") | {"order_id"})
            self.assertEqual(arguments("cancel_order"), {"order_id"})
            self.assertEqual(arguments("cancel_all_orders"), {"within_seconds"})
            self.assertEqual(arguments("set_dead_man_switch"), {"seconds"})
            self.assertEqual(arguments("set_leverage"), {"leverage"})
            self.assertEqual(arguments("set_fee_mode"), {"mode"})
            prompts = {prompt.name for prompt in (await client.list_prompts()).prompts}
            self.assertEqual(prompts, {"onboard", "trading_session", "build_bot", "hackathon_plan"})
            snapshot = await client.call_tool("get_market", {"depth": 1, "trades": 1})
            self.assertFalse(snapshot.is_error)
            self.assertEqual(snapshot.structured_content["best_bid"]["price"], "79999.9")
            self.assertEqual(snapshot.structured_content["rules"]["tick_size"], "0.1")
            self.assertTrue(snapshot.structured_content["matches_installed_pins"])
            refused = await client.call_tool("place_order", self.order)
            self.assertTrue(refused.is_error)
            self.assertIn("FAIRVENUE_TRADING=off", refused.content[0].text)
            rules = await client.call_tool("get_guide", {"topic": "hackathon"})
            self.assertIn("November 16, 17 and 18, 2026 (UTC)", rules.content[0].text)
            self.assertIn("50+ participants", rules.content[0].text)
        self.assertEqual(self.fixture.calls["admit"], 0)

    async def test_first_order_asks_once_in_a_dialog_then_trades(self) -> None:
        # "auto" negotiates 2026-07-28 (input round trip), "legacy" 2025-11-25 (mid-call).
        for mode in ("auto", "legacy"):
            with self.subTest(mode=mode):
                self.fixture = NativeFixture()
                await self.dialog_flow(mode)

    async def dialog_flow(self, mode: str) -> None:
        from mcp import Client
        from mcp_types import ElicitResult

        from fairvenue_mcp.server import create_server

        trader = self.trader("ask")
        trader.import_key(str(trader.config.download_dirs[0] / "fairvenue-TESTNET-agent.json"))
        asked: list[str] = []

        async def user_clicks_accept(context: Any, params: Any) -> ElicitResult:
            asked.append(params.message)
            return ElicitResult(
                action="accept", content={"allow": True, "max_btc_per_order": 0.002}
            )

        server = create_server(trader)
        async with (
            asyncio.timeout(60),
            Client(server, mode=mode, elicitation_callback=user_clicks_accept) as c,
        ):
            preview = await c.call_tool("place_order", {**self.order, "dry_run": True})
            self.assertFalse(preview.is_error)
            self.assertEqual(asked, [])  # A dry run never asks.
            first = await c.call_tool("place_order", self.order)
            self.assertFalse(first.is_error, first.content)
            self.assertEqual(first.structured_content["status"], "accepted")
            order_id = first.structured_content["order_id"]
            self.assertEqual(len(asked), 1)
            self.assertIn("FairVenue TESTNET", asked[0])
            self.assertEqual(trader.limits_view()["max_order_quantity_btc"], "0.002")
            second = await c.call_tool("cancel_order", {"order_id": order_id})
            self.assertEqual(second.structured_content["status"], "accepted")
            self.assertEqual(len(asked), 1)  # Asked once per session.
            all_orders = await c.call_tool("cancel_all_orders", {"within_seconds": 5})
            self.assertEqual(all_orders.structured_content["status"], "accepted")
            modify = await c.call_tool("modify_order", {**self.order, "order_id": order_id})
            self.assertTrue(modify.is_error)
            self.assertIn("Cancel the order", modify.content[0].text)
            stopped = await c.call_tool("disable_trading", {})
            self.assertEqual(stopped.structured_content["status"], "paused")
        self.assertEqual(self.fixture.calls["admit"], 3)

    async def test_declining_or_a_client_without_dialogs_signs_nothing(self) -> None:
        from mcp import Client
        from mcp_types import ElicitResult

        from fairvenue_mcp.server import create_server

        trader = self.trader("ask")
        trader.import_key(str(trader.config.download_dirs[0] / "fairvenue-TESTNET-agent.json"))

        async def user_declines(context: Any, params: Any) -> ElicitResult:
            return ElicitResult(action="decline")

        async with (
            asyncio.timeout(60),
            Client(create_server(trader), elicitation_callback=user_declines) as client,
        ):
            declined = await client.call_tool("place_order", self.order)
            self.assertTrue(declined.is_error)
            self.assertIn("did not allow trading", declined.content[0].text)
        async with asyncio.timeout(60), Client(create_server(trader)) as client:
            unsupported = await client.call_tool("cancel_all_orders", {})
            self.assertTrue(unsupported.is_error)
            self.assertIn("FAIRVENUE_TRADING=on", unsupported.content[0].text)
        self.assertEqual(self.fixture.calls["admit"], 0)
        self.assertEqual(trader.trading_state(), "needs_confirmation")


class McpConfigAndMarketTests(unittest.TestCase):
    def test_configuration_is_explicit(self) -> None:
        config = load_config({"FAIRVENUE_HOME": "/tmp/x/../fairvenue"})
        self.assertEqual(str(config.home), "/tmp/fairvenue")
        self.assertEqual(config.trading_mode, "ask")
        self.assertTrue(str(load_config({}).home).endswith(".fairvenue/ts2-testnet"))
        self.assertEqual(load_config({"FAIRVENUE_TRADING": "on"}).trading_mode, "on")
        self.assertEqual(load_config({"FAIRVENUE_TRADING": "off"}).trading_mode, "off")
        self.assertEqual(str(config.limits.max_order_notional), "1000")
        for name, value in (
            ("FAIRVENUE_TRADING", "maybe"),
            ("FAIRVENUE_MAX_ORDER_QUANTITY", "-1"),
            ("FAIRVENUE_MAX_ORDER_NOTIONAL", "NaN"),
            ("FAIRVENUE_INSTALLATION_SHA256", "abc"),
        ):
            with self.subTest(name=name), self.assertRaises(ConfigError):
                load_config({name: value})

    def test_packaged_pins_are_the_ts2_deployment(self) -> None:
        pins = json.loads(profile_module.packaged_installation())
        self.assertEqual(
            {key: pins[key] for key in ("chain_id", "settlement", "config_hash")}, ts2.PINS
        )
        self.assertEqual(pins["api_url"], "https://app.fairvenue.xyz/")

    def test_market_snapshots_must_match_the_installed_deployment(self) -> None:
        fixture = NativeFixture()
        good = market(fixture)
        parsed = parse_market(good, ts2.SETTLEMENT)
        assert parsed.best_ask is not None
        self.assertEqual(parsed.best_ask.price.to_eng_string(), "80000.2")
        self.assertEqual((parsed.tick_size, parsed.step_size), (Decimal("0.1"), Decimal("0.001")))
        self.assertEqual(parsed.config_hash, ts2.CONFIG_HASH)
        for change in (
            {"settlement": "0x" + "1" * 40},
            {"schema": "fairvenue-operator-beta-market-v1"},
            {"proof_backed": True},
            {"source": "operator_trusted_testnet_soft_snapshot"},
            {"asks": [{"price": "79999.9", "quantity": "1"}]},
            {"bids": [{"price": "1", "quantity": "1"}, {"price": "2", "quantity": "1"}]},
            {"mark_price": 80000.05},
            {"admission": {"open": True, "reasons": ["mark_lagging"]}},
            {"config_hash": "0x1"},
        ):
            with self.subTest(change=change), self.assertRaises(MarketDataError):
                parse_market({**good, **change}, ts2.SETTLEMENT)


if __name__ == "__main__":
    os.environ.setdefault("PYTHONDONTWRITEBYTECODE", "1")
    unittest.main()
