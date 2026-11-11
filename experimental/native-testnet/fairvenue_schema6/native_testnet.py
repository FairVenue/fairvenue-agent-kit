"""Explicit TS2 operator-beta TESTNET agent client, not the source-closed client.

No V0/V4 request translation, Google cookies, wallet signing, faucet or withdrawal.
One exact public signed original is durable before admission; recovery never resends.
"""

from __future__ import annotations

import asyncio
import copy
import hashlib
import inspect
import os
import secrets
import sqlite3
import stat
import time
import weakref
from urllib.parse import quote

from . import ts2
from .client import _json, _snapshot
from .crypto import CryptoPrimitives
from .key_file import _native_api_url, load_native_testnet_agent_key
from .protocol import AdmitRefusal, AgentError, encoded_hex, exact, fail, integer
from .transport import HttpTransport

_TABLE = ("CREATE TABLE original(singleton INTEGER PRIMARY KEY CHECK(singleton=1),"
          "config BLOB NOT NULL CHECK(length(config)=32),revision INTEGER NOT NULL CHECK(revision>=0),"
          "highest INTEGER NOT NULL CHECK(highest>=0),pending BLOB CHECK(pending IS NULL OR "
          "(length(pending)>0 AND length(pending)<=16384)))")
_SIGNERS = weakref.WeakKeyDictionary()
PENDING_SCHEMA = "fairvenue-ts2-public-original-v1"
#: Browser parity: a resting order lives up to 23 hours (never past its key),
#: an IOC, FOK or MARKET order and a security command 30 seconds.
GTC_LIFETIME_MS = 23 * 60 * 60 * 1000
ONE_SHOT_LIFETIME_MS = 30_000
_RIGHTS = {"order": ts2.TRADE, "set_fee_mode": ts2.TRADE, "set_leverage": ts2.TRADE,
           "cancel": ts2.CANCEL, "kill_all": ts2.CANCEL, "set_dms": ts2.DEAD_MAN_SWITCH}
_TIME_IN_FORCE = {"GTC": 0, "IOC": 1, "FOK": 2}
_INFO = "/api/v1/info"
_READ = "/v1/ts2/agent/read"
#: Browser parity: a signed account read is valid for 30 seconds (server cap 60 s).
READ_LIFETIME_MS = 30_000


def _wire(value):
    data = ts2.canonical(value).encode()
    if not data or len(data) > 8192:
        fail()
    return data


def _file(info):
    if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
            or info.st_nlink != 1 or stat.S_IMODE(info.st_mode) != 0o600):
        fail()


def _nonce():
    """A fresh random u64 (orders and kill-all deduplicate by it)."""
    return str(secrets.randbits(64) or 1)


def _order(value):
    """The order shape kept from earlier releases: decimal price and quantity.

    A MARKET order with ``price`` None may add ``max_slippage_bps`` (1 to 1,000,
    default 50, the browser's 0.5%): the signed bound is that far beyond the
    best opposite price, or the mark when that side is empty.
    """
    optional = ("max_slippage_bps",) if type(value) is dict and "max_slippage_bps" in value else ()
    exact(value, ("symbol", "side", "order_type", "time_in_force", "price", "quantity",
                  "reduce_only", "post_only", *optional))
    market = value["order_type"] == "MARKET"
    if (value["symbol"] != "BTCUSDC" or value["side"] not in ("BUY", "SELL")
            or value["order_type"] not in ("LIMIT", "MARKET")
            or type(value["time_in_force"]) is not str
            or value["time_in_force"] not in _TIME_IN_FORCE
            or type(value["reduce_only"]) is not bool or type(value["post_only"]) is not bool
            or type(value["quantity"]) is not str
            or (market and (value["time_in_force"] == "GTC" or value["post_only"]))
            or (value["post_only"] and value["time_in_force"] != "GTC")
            or not (type(value["price"]) is str or (market and value["price"] is None))
            or (optional and not (market and value["price"] is None))):
        fail("invalid_order")
    if optional:
        ts2.slippage_bps(value["max_slippage_bps"])
    return dict(value)


def _admitted_while_closed(reasons, chosen):
    """Whether ``chosen`` may still be sent while admission is closed.

    Cancels and kill-all always go to the server, which decides (they reduce
    risk, and ``funding_owed`` admits only them). A lagging mark
    (``mark_lagging`` alone) also admits reduce-only orders. Everything else
    is refused locally before anything is signed.
    """
    kind = chosen["type"]
    if kind in ("cancel", "kill_all"):
        return True
    return kind == "place" and chosen["order"]["reduce_only"] and reasons == ["mark_lagging"]


def _ui_action(value):
    """One public action, checked before any I/O. ``modify`` does not exist on TS2."""
    kind = value.get("type") if type(value) is dict else None
    if kind == "modify":
        fail("unsupported")
    if kind == "place":
        exact(value, ("type", "order"))
        return {"type": "place", "order": _order(value["order"])}
    if kind == "cancel":
        exact(value, ("type", "order_id"))
        ts2.fixed(value["order_id"], 32, nonzero=True)  # The 0x TS2 order id.
    elif kind == "kill_all":
        exact(value, ("type",))
    elif kind == "fee_mode":
        exact(value, ("type", "mode"))
        if value["mode"] not in ("FIXED", "PNL"):
            fail()
    elif kind == "leverage":
        exact(value, ("type", "leverage"))
        if int(ts2.uint(value["leverage"], nonzero=True)) > 100:
            fail()
    elif kind == "dead_man_switch":
        exact(value, ("type", "deadline_ms"))
        ts2.uint(value["deadline_ms"])
    else:
        fail()
    return dict(value)


class _OriginalCustody:
    """Private local continuity, not proof or rollback attestation."""

    def __init__(self, path, config, initialize):
        import fcntl  # POSIX custody is explicit; importing closed clients stays portable.
        self.connection, self.lock, self.file = None, None, None
        if (type(initialize) is not bool or type(path) is not str or not os.path.isabs(path)
                or os.path.normpath(path) != path or os.path.realpath(os.path.dirname(path)) != os.path.dirname(path)):
            fail()
        directory = os.lstat(os.path.dirname(path))
        if (not stat.S_ISDIR(directory.st_mode) or directory.st_uid != os.getuid()
                or stat.S_IMODE(directory.st_mode) != 0o700):
            fail()
        self.path, self.config = path, config
        try:
            family = (path, path + ".lock", path + "-wal", path + "-shm", path + "-journal")
            if initialize and any(os.path.lexists(name) for name in family):
                fail()
            for name in family:
                if os.path.lexists(name):
                    _file(os.lstat(name))
            flags = os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC
            flags |= os.O_CREAT | os.O_EXCL if initialize else 0
            self.lock = os.open(path + ".lock", flags, 0o600)
            _file(os.fstat(self.lock))
            fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.file = os.open(path, flags, 0o600)
            _file(os.fstat(self.file))
            self.inode = (os.fstat(self.file).st_dev, os.fstat(self.file).st_ino)
            if not initialize:
                # No WAL recovery or write pragma until exact fixed schema and
                # installed configuration pass a read-only inspection.
                probe = sqlite3.connect("file:" + quote(path, safe="/") + "?mode=ro", uri=True)
                try:
                    self._identity(probe)
                finally:
                    probe.close()
            self.connection = sqlite3.connect(path, isolation_level=None)
            self.connection.execute("PRAGMA journal_mode=WAL")
            self.connection.execute("PRAGMA synchronous=FULL")
            self.connection.execute("PRAGMA wal_autocheckpoint=64")
            self.connection.execute("PRAGMA max_page_count=256")
            if initialize:
                self.connection.execute("PRAGMA application_id=1180062258")
                self.connection.execute("PRAGMA user_version=1")
                self.connection.execute("BEGIN IMMEDIATE")
                try:
                    self.connection.execute(_TABLE)
                    self.connection.execute("INSERT INTO original VALUES(1,?,0,0,NULL)", (config,))
                    self.connection.execute("COMMIT")
                except BaseException:
                    self.connection.execute("ROLLBACK")
                    raise
                descriptor = os.open(os.path.dirname(path), os.O_RDONLY | os.O_DIRECTORY)
                try:
                    os.fsync(descriptor)
                finally:
                    os.close(descriptor)
            self._identity(self.connection)
            self.read()
        except BaseException:
            self.close()
            raise AgentError("unavailable") from None

    def _identity(self, connection):
        if (connection.execute("PRAGMA application_id").fetchone() != (1180062258,)
                or connection.execute("PRAGMA user_version").fetchone() != (1,)
                or connection.execute("PRAGMA quick_check").fetchone() != ("ok",)
                or connection.execute("SELECT type,name,sql FROM sqlite_schema WHERE name NOT LIKE 'sqlite_%' ORDER BY name").fetchall()
                != [("table", "original", _TABLE)]
                or connection.execute("SELECT config FROM original WHERE singleton=1").fetchone() != (self.config,)):
            fail()

    def read(self):
        _file(os.lstat(self.path))
        if (os.lstat(self.path).st_dev, os.lstat(self.path).st_ino) != self.inode:
            fail()
        if self.connection.execute("SELECT count(*) FROM original").fetchone() != (1,):
            fail()
        row = self.connection.execute("SELECT config,revision,highest,pending FROM original WHERE singleton=1").fetchone()
        if row is None or row[0] != self.config:
            fail()
        revision, highest = integer(row[1]), integer(row[2])
        pending = _json(row[3]) if row[3] is not None else None
        return revision, highest, pending

    def change(self, revision, highest, pending):
        integer(revision); integer(highest)
        data = _snapshot(pending) if pending is not None else None
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            changed = self.connection.execute("UPDATE original SET revision=?,highest=?,pending=? WHERE singleton=1 AND revision=? AND config=?",
                                              (revision + 1, highest, data, revision, self.config)).rowcount
            if changed != 1:
                fail()
            self.connection.execute("COMMIT")
        except BaseException:
            self.connection.execute("ROLLBACK")
            raise
        if self.read() != (revision + 1, highest, pending):
            fail()

    def close(self):
        if self.connection is not None:
            self.connection.close()
            self.connection = None
        for name in ("file", "lock"):
            descriptor = getattr(self, name, None)
            if descriptor is not None:
                os.close(descriptor)
                setattr(self, name, None)


class NativeTestnetClient:
    """Distinct explicit TS2 TESTNET client; installed pins are not discovered.

    Use from_credentials with a downloaded TS2 bot key, the independently
    approved deployment pins and API URL and a private continuity path. No
    request occurs at creation. Separate processes/strategies require separate
    agent keys and custody. A V4 key file is refused ("TS2: create a new bot key").
    """

    @classmethod
    def from_credentials(cls, credential_path, *, policy, approved_api_url, custody_path,
                         initialize=False):
        api = _native_api_url(approved_api_url)
        signer, grant = load_native_testnet_agent_key(credential_path, policy=policy,
                                                     approved_api_url=api)
        return cls(policy=policy, grant=grant, signer=signer, transport=HttpTransport(api[:-1]),
                   approved_api_url=api, custody_path=custody_path, initialize=initialize)

    def __init__(self, *, policy, grant, signer, transport, approved_api_url,
                 custody_path, initialize=False):
        self._api = _native_api_url(approved_api_url)
        self._crypto = CryptoPrimitives()
        self._pins = ts2.pinned(policy)
        self._grant = ts2.grant_copy(grant, self._pins)
        material = self._grant["material"]
        if (getattr(signer, "public_key", None) != material["agent_public_key"]
                or not self._crypto.strong(material["agent_public_key"])
                or not callable(getattr(signer, "sign_bytes", None))
                or not callable(getattr(transport, "post", None))):
            fail()
        # Exact published deployment pins, never discovered from an API response.
        self.settlement, self.config_hash = self._pins["settlement"], self._pins["config_hash"]
        self._busy, self._inflight, self._loop, self._closed = False, set(), None, False
        self._last_context = None
        self._signer, self._post, self._market = signer, transport.post, None
        if signer in _SIGNERS:
            fail("busy")
        identity = hashlib.sha256(b"FairVenue.ts2.sdk-custody.v1\0" + ts2.canonical(
            {"api_url": self._api, "pins": self._pins, "grant": self._grant}).encode()).digest()
        try:
            self._custody = _OriginalCustody(custody_path, identity, initialize)
        except Exception:
            raise AgentError("unavailable") from None
        try:
            self._original()
            _SIGNERS[signer] = weakref.ref(self)
        except BaseException:
            self._custody.close()
            raise

    def __repr__(self):
        return "<NativeTestnetClient TS2 TESTNET operator-beta: secrets not exported>"

    def close(self):
        if self._busy or self._inflight:
            fail("busy")
        if not self._closed:
            self._custody.close()  # A pending original remains on disk.
            _SIGNERS.pop(self._signer, None)
            self._closed = True

    def has_pending_request(self):
        try:
            return self._original() is not None
        except Exception:
            raise AgentError("outcome_unknown") from None

    def _original(self):
        pending = self._custody.read()[2]
        if pending is None:
            return None
        exact(pending, ("schema", "request"))
        if pending["schema"] != PENDING_SCHEMA:
            fail()
        # Locally re-authenticated: exact pins, this key and its own signature.
        request = ts2.request_copy(pending["request"], self._pins, self._crypto)
        material = self._grant["material"]
        if (request["account_key"] != material["account_key"]
                or request["agent_public_key"] != material["agent_public_key"]):
            fail("signature_invalid")
        return pending

    async def _operation(self, timeout_ms, work):
        if self._closed or self._busy or self._inflight:
            fail("busy")
        if type(timeout_ms) is not int or not 1 <= timeout_ms <= 60000:
            fail()
        loop = asyncio.get_running_loop()
        if self._loop is not None and self._loop is not loop:
            fail("context_changed")
        self._loop, self._busy = loop, True
        deadline = time.monotonic() + timeout_ms / 1000

        def check():
            if time.monotonic() >= deadline:
                fail("deadline")

        async def bounded(callback):
            check()
            async def invoke():
                result = callback()
                if not inspect.isawaitable(result):
                    fail()
                return await result
            task = asyncio.create_task(invoke())
            self._inflight.add(task)
            def done(completed):
                self._inflight.discard(completed)
                if not completed.cancelled():
                    completed.exception()  # Consume without secret/upstream output.
            task.add_done_callback(done)
            complete, _ = await asyncio.wait({task}, timeout=max(0,deadline-time.monotonic()))
            if not complete:
                # A still-running callback retains the signer/transport fence.
                fail("deadline")
            result = task.result()
            check()
            return result

        async def post(path, value):
            data = await bounded(lambda:self._post(path,_wire(value),min(15,deadline-time.monotonic())))
            return ts2.parse_json(data, 131072 if path in (_INFO, _READ) else 16384)

        async def sign(message):
            signature = await bounded(lambda:self._signer.sign_bytes(message))
            agent = self._grant["material"]["agent_public_key"]
            if type(signature) is not bytes or not self._crypto.verify(encoded_hex(signature),message,agent):
                fail("signature_invalid")
            return encoded_hex(signature)

        try:
            return await work(post,sign,check)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            # Nothing can erase a public original after a failed callback.
            try:
                pending = self._custody.read()[2] is not None
            except Exception:
                pending = True
            code = "outcome_unknown" if pending else error.code if isinstance(error,AgentError) else "unavailable"
            raise AgentError(code) from None
        finally:
            self._busy = False

    async def _context(self, post, sign, highest):
        material = self._grant["material"]
        challenge = encoded_hex(secrets.token_bytes(32))
        if challenge == "0x" + "00" * 32:
            fail("unavailable")
        signature = await sign(ts2.context_signing_bytes(self._pins, material["agent_public_key"], challenge))
        reply = await post("/v1/agent/context", {"schema":ts2.CONTEXT_REQUEST_SCHEMA,
                           "chain_id":self._pins["chain_id"],"settlement":self._pins["settlement"],
                           "agent_public_key":material["agent_public_key"],"challenge":challenge,
                           "signature":signature})
        context, server_next = ts2.context_copy(reply, self._pins, self._grant, challenge)
        self._last_context = copy.deepcopy(context)
        # Never below a security command nonce this key already signed.
        next_nonce = max(int(server_next), highest + 1)
        if next_nonce > ts2.U64:
            fail("context_changed")
        return {"context":context, "public_grant":copy.deepcopy(self._grant),
                "next_nonce":str(next_nonce), "next_command_nonce":server_next}

    def last_context(self):
        """The latest validated signed context (a copy), or None before any.

        After a local or server ``admission_closed`` its ``admission["reasons"]``
        says why. Public data only, never admission authority.
        """
        return copy.deepcopy(self._last_context)

    async def account(self, *, with_context=True, timeout_ms=15000):
        """The bot-signed account read on ``/v1/ts2/agent/read``. No order or nonce.

        Returns ``{"context", "view"}``: the signed context (fee mode, leverage,
        admission, or None without ``with_context``) and the strict view
        (``ts2.view_copy``): balances and margins, the position, the latest
        orders and fills with their settlement state, the book and the mark.
        Works while an original is pending. Soft, operator-trusted state.
        """
        if type(with_context) is not bool:
            fail()

        async def work(post, sign, check):
            context = None
            if with_context:
                context = (await self._context(post, sign, self._custody.read()[1]))["context"]
            material = self._grant["material"]
            now = int(time.time() * 1000)
            expires = min(now + READ_LIFETIME_MS, int(material["expires_at_ms"]))
            if expires <= now:
                fail("context_changed")
            account_key = material["account_key"]
            message = ts2.read_signing_message(self._pins, account_key, str(expires))
            request = ts2.read_request(self._pins, account_key, material["agent_public_key"],
                                       str(expires), await sign(message), self._crypto)
            view = ts2.view_copy(await post(_READ, request), self._pins)
            return {"context": context, "view": view}
        return await self._operation(timeout_ms, work)

    async def snapshot(self, *, timeout_ms=15000):
        """Signed agent context: account fee mode, leverage, batch, admission, the
        public grant and ``next_nonce`` (the next security command nonce). No order."""
        async def work(post,sign,check):
            return await self._context(post,sign,self._custody.read()[1])
        return await self._operation(timeout_ms,work)

    async def _rules(self, post, fresh):
        """Tick and lot sizes of the pinned market; best prices only when fresh."""
        if self._market is not None and not fresh:
            return self._market
        rules = ts2.market_rules(await post(_INFO,{"type":"native_market"}), self._pins)
        if self._market is not None and (rules["tick_size"],rules["step_size"]) != (
                self._market["tick_size"],self._market["step_size"]):
            fail("context_changed")
        self._market = {"tick_size":rules["tick_size"],"step_size":rules["step_size"],
                        "best_bid":None,"best_ask":None,"mark_price":None}
        return rules

    async def market_rules(self, *, timeout_ms=15000):
        """The public tick and lot sizes and best prices the SDK signs orders with."""
        async def work(post,sign,check):
            return await self._rules(post,True)
        return await self._operation(timeout_ms,work)

    def _ts2_action(self, chosen, nonce, expires, context, rules):
        material = self._grant["material"]
        kind = chosen["type"]
        if kind == "place":
            order = chosen["order"]
            side = order["side"]
            if order["order_type"] == "MARKET":
                if order["price"] is None:
                    # Browser parity: the best opposite price, else the mark.
                    opposite = rules["best_ask"] if side == "BUY" else rules["best_bid"]
                    reference = opposite if opposite is not None else rules["mark_price"]
                    if reference is None:
                        fail("invalid_order")  # Nothing to bound the order against.
                    ticks = ts2.protection_ticks(
                        reference, rules["tick_size"], side,
                        order.get("max_slippage_bps", ts2.DEFAULT_SLIPPAGE_BPS))
                else:
                    # A chosen bound rounds away from the book: never tighter than chosen.
                    ticks = ts2.units(order["price"], rules["tick_size"],
                                      "up" if side == "BUY" else "down")
            else:
                ticks = ts2.units(order["price"], rules["tick_size"])
            return {"type":"order","delegation_nonce":material["nonce"],"client_nonce":_nonce(),
                    "side":0 if side == "BUY" else 1,"kind":1 if order["order_type"] == "MARKET" else 0,
                    "time_in_force":_TIME_IN_FORCE[order["time_in_force"]],
                    "post_only":order["post_only"],"reduce_only":order["reduce_only"],
                    "fee_mode":context["fee_mode"],"limit_price_ticks":ticks,
                    "quantity_lots":ts2.units(order["quantity"], rules["step_size"]),
                    "expires_at_ms":expires}
        if kind == "cancel":
            return {"type":"cancel","order_id":chosen["order_id"]}
        if kind == "kill_all":
            return {"type":"kill_all","client_nonce":_nonce()}
        security = {"delegation_nonce":material["nonce"],"command_nonce":nonce,"expires_at_ms":expires}
        if kind == "dead_man_switch":
            return {"type":"set_dms",**security,"deadline_ms":chosen["deadline_ms"]}
        if kind == "fee_mode":
            return {"type":"set_fee_mode",**security,"fee_mode":0 if chosen["mode"] == "FIXED" else 1}
        return {"type":"set_leverage",**security,"leverage":chosen["leverage"]}

    async def _recover(self, post):
        original = self._original()
        if original is None:
            fail("no_pending_request")
        request = original["request"]
        result = ts2.recovery_copy(await post("/v1/agent/recovery", request), request,
                                   ts2.request_sha256(request))
        if result["status"] in ts2.TERMINAL:
            revision,highest,stored = self._custody.read()
            if stored != original:
                fail("context_changed")
            self._custody.change(revision,highest,None)
        return copy.deepcopy(result)

    async def recover_original(self, *, timeout_ms=15000):
        """Exact original lookup only, even after expiry/revocation/a new batch.

        ``accepted`` (journaled and applied), ``rejected`` (journaled native
        rejection), ``not_placed`` (never journaled and past its own expiry, so
        nothing can admit it any more) and ``not_settled`` (its batch was voided,
        it never takes effect) clear it. ``unresolved`` retains it. The result's
        ``reason`` is the server's fixed ``not_placed`` reason, else None. This
        method never sends admit.
        """
        async def work(post,sign,check):
            return await self._recover(post)
        return await self._operation(timeout_ms,work)

    def _not_placed(self, refusal, pending):
        """Definitive admit refusal of exactly the retained original.

        Only an exact ``not_placed`` body (HTTP 409, or 429 for ``rate_limited``)
        whose ``request_sha256`` is this original's digest clears it. Raises
        ``AgentError(reason)``. Anything else returns, keeping the original.
        """
        try:
            value = ts2.parse_json(refusal.body)
        except AgentError:
            return
        reason = ts2.refusal_reason(value, refusal.status, ts2.request_sha256(pending["request"]))
        if reason is None:
            return
        revision,highest,stored = self._custody.read()
        if stored != pending:
            return
        self._custody.change(revision,highest,None)
        fail(reason)

    async def run(self, input, *, timeout_ms=60000):
        """One explicitly authorized TS2 action; no automatic resend or reprice.

        ``input`` keeps its earlier shape ``{"nonce", "expiresAtMs", "action"}``:
        ``nonce`` is ``snapshot()["next_nonce"]`` and ``expiresAtMs`` the signed
        expiry (a resting order's lifetime, or a security command's deadline).
        Actions: ``place`` (decimal price and quantity that must fit the market
        tick and lot exactly, ``invalid_order`` otherwise), ``cancel`` (the 0x
        TS2 order id), ``kill_all``, ``fee_mode``, ``leverage`` ("1".."100") and
        ``dead_man_switch``. ``modify`` raises ``unsupported``: cancel and place
        a new order. A definitive ``not_placed`` refusal clears the original and
        raises ``AgentError`` with its reason: ``invalid_request``, ``rejected``,
        ``admission_closed`` or ``rate_limited`` (sign a fresh request). Any
        other failure is ``outcome_unknown``: the original stays retained until
        ``recover_original()`` resolves it. While the signed context shows
        admission closed, only cancels, kill-all and (under ``mark_lagging``)
        reduce-only orders are signed. Anything else raises ``admission_closed``
        before signing: ``last_context()`` names the reason.
        """
        exact(input,("nonce","expiresAtMs","action"))
        chosen = _ui_action(input["action"])
        nonce = ts2.uint(input["nonce"],nonzero=True)
        expires = ts2.uint(input["expiresAtMs"],nonzero=True)
        return await self._operation(timeout_ms, lambda post,sign,check: self._admit(
            post,sign,check,chosen,lambda snapshot: (nonce,expires)))

    async def submit(self, action, *, lifetime_ms, timeout_ms=60000):
        """``run`` with ONE signed context read: its nonce and expiry come from it.

        ``action`` is a ``run`` action. The expiry is ``lifetime_ms`` after the
        later of this machine's clock and the server's protocol time, never past
        the key's expiry: up to 23 hours for a resting order, 30 seconds for a
        one-shot order or account command (the browser's lifetimes). Results and
        errors are exactly those of ``run``.
        """
        chosen = _ui_action(action)
        if type(lifetime_ms) is not int or not 1_000 < lifetime_ms <= GTC_LIFETIME_MS:
            fail()

        def pick(snapshot):
            now = max(int(time.time() * 1000), int(snapshot["context"]["protocol_time_ms"]))
            key_expiry = int(self._grant["material"]["expires_at_ms"])
            return snapshot["next_nonce"], str(min(now + lifetime_ms, key_expiry))
        return await self._operation(timeout_ms, lambda post,sign,check: self._admit(
            post,sign,check,chosen,pick))

    async def _admit(self, post, sign, check, chosen, pick):
        """One signed context read, then at most one signature and one admit."""
        if self._original() is not None:
            fail("outcome_unknown")
        revision,highest,_ = self._custody.read()
        snapshot = await self._context(post,sign,highest)
        nonce, expires = pick(snapshot)
        if nonce != snapshot["next_nonce"]:
            fail("nonce_reused")
        context, material = snapshot["context"], self._grant["material"]
        kind = chosen["type"]
        admission = context["admission"]
        if not admission["open"] and not _admitted_while_closed(admission["reasons"], chosen):
            fail("admission_closed")  # Nothing is signed. last_context() says why.
        if kind in ("fee_mode","leverage") and context["account_settings_enabled"] is False:
            fail("admission_closed")  # The server would refuse it. Nothing is signed.
        if (kind not in ("cancel","kill_all")
                and not int(context["protocol_time_ms"]) < int(expires) <= int(material["expires_at_ms"])):
            fail("context_changed")
        rules = None
        if kind == "place":
            order = chosen["order"]
            rules = await self._rules(post, order["order_type"] == "MARKET" and order["price"] is None)
        action = self._ts2_action(chosen,nonce,expires,context,rules)
        if not material["permissions"] & _RIGHTS[action["type"]]:
            fail()
        message, order_id = ts2.signing_message(self._pins,material["account_key"],
                                                material["agent_public_key"],action,self._crypto)
        request = ts2.request_copy({"schema":ts2.REQUEST_SCHEMA,**self._pins,
                                    "account_key":material["account_key"],
                                    "agent_public_key":material["agent_public_key"],
                                    "signature":await sign(message),"action":action},
                                   self._pins,self._crypto)
        check()
        pending = {"schema":PENDING_SCHEMA,"request":request}
        # A signed security nonce is never signed again, whatever its outcome.
        floor = max(highest,int(nonce)) if action["type"] in ts2.SECURITY else highest
        self._custody.change(revision,floor,pending)
        self._original()  # Read back and locally authenticate before send.
        try:
            reply = await post("/v1/agent/admit",request)
        except AdmitRefusal as refusal:
            self._not_placed(refusal,pending)
            raise
        ack = ts2.ack_copy(reply,request,ts2.request_sha256(request),order_id)
        recovery = await self._recover(post)
        if recovery["status"] not in ts2.TERMINAL:
            fail("outcome_unknown")
        return {"ack":ack,"recovery":recovery}
