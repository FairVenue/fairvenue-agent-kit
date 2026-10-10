"""Explicit deployed operator-beta TESTNET client, not the source-closed client.

No V0 request translation, Google cookies, wallet signing, faucet or withdrawal.
One exact public original is durable before admission; recovery never resends.
"""

from __future__ import annotations

import asyncio
import copy
import hashlib
import inspect
import json
import os
import secrets
import sqlite3
import stat
import time
import weakref
from urllib.parse import quote

from .client import AgentClient, _json, _snapshot
from .context import REQUEST_SCHEMA, RESPONSE_SCHEMA, _scope, _signing_bytes
from .crypto import CryptoPrimitives
from .key_file import (_NATIVE_MOCK_TOKEN, _NATIVE_SETTLEMENT, _native_api_url,
                       load_native_testnet_agent_key)
from .protocol import (AgentError, action_copy, build, context_copy, decimal,
                       encoded_hex, exact, fail, grant_copy, integer, jcs,
                       policy_copy)
from .transport import HttpTransport

_TABLE = ("CREATE TABLE original(singleton INTEGER PRIMARY KEY CHECK(singleton=1),"
          "config BLOB NOT NULL CHECK(length(config)=32),revision INTEGER NOT NULL CHECK(revision>=0),"
          "highest INTEGER NOT NULL CHECK(highest>=0),pending BLOB CHECK(pending IS NULL OR "
          "(length(pending)>0 AND length(pending)<=16384)))")
_SIGNERS = weakref.WeakKeyDictionary()


def _wire(value):
    data = jcs(value).encode("utf-8")
    if not data or len(data) > 8192:
        fail()
    return data


def _file(info):
    if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
            or info.st_nlink != 1 or stat.S_IMODE(info.st_mode) != 0o600):
        fail()


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
    """Distinct explicit native TESTNET client; installed pins are not discovered.

    Use from_credentials with a downloaded TESTNET key, independently approved
    policy/API URL and private continuity path. No request occurs at creation.
    Separate processes/strategies require separate agent keys and custody.
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
        self._policy = policy_copy(_json(_snapshot(policy)), self._crypto)
        self._grant = grant_copy(_json(_snapshot(grant)), self._policy, self._crypto)
        if (encoded_hex(self._policy["domain"]["settlement"]) != _NATIVE_SETTLEMENT
                or self._grant["settlement"] != _NATIVE_SETTLEMENT
                or getattr(signer, "public_key", None) != self._grant["agentPublicKey"]
                or not callable(getattr(signer, "sign_bytes", None))
                or not callable(getattr(transport, "post", None))):
            fail()
        # The mock token is an exact published deployment pin, never a live
        # token/account address discovered from an API response.
        self.mock_token = _NATIVE_MOCK_TOKEN
        self._busy, self._inflight, self._loop, self._closed = False, set(), None, False
        self._time_floor, self._nonce_floor = 0, 0
        self._signer, self._post = signer, transport.post
        if signer in _SIGNERS:
            fail("busy")
        identity = hashlib.sha256(_snapshot({"api_url":self._api, "policy":policy, "grant":grant})).digest()
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
        return "<NativeTestnetClient TESTNET operator-beta: secrets not exported>"

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
        exact(pending, ("schema", "request", "protocol_time_ms"))
        if pending["schema"] != "fairvenue-native-testnet-public-original-v1":
            fail()
        request = pending["request"]
        exact(request, ("intent", "intent_signature", "ordinary_signature", "generation_signature"))
        intent = request["intent"]
        exact(intent, ("schema", "expected", "agent_public_key", "nonce", "expires_at_ms", "action"))
        context = {"authenticated":True, "session_scope":_scope(self._policy,self._grant),
                   "owner":self._grant["owner"], "prefix":intent["expected"],
                   "protocol_time_ms":pending["protocol_time_ms"]}
        signables = build(self._policy,self._grant,context,
                          {"nonce":intent["nonce"], "expiresAtMs":intent["expires_at_ms"], "action":intent["action"]}, self._crypto)
        if signables["intent"] != intent:
            fail("signature_invalid")
        for signature, message in (("intent_signature","intent_signing_bytes"),
                                   ("ordinary_signature","ordinary_signing_hash"),
                                   ("generation_signature","generation_signing_bytes")):
            if not self._crypto.verify(request[signature], bytes.fromhex(signables[message][2:]), self._grant["agentPublicKey"]):
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
            return _json(await bounded(lambda:self._post(path,_wire(value),min(15,deadline-time.monotonic()))))

        async def sign(message):
            raw = bytes.fromhex(message[2:])
            signature = await bounded(lambda:self._signer.sign_bytes(raw))
            if type(signature) is not bytes or not self._crypto.verify(encoded_hex(signature),raw,self._grant["agentPublicKey"]):
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

    async def _context(self, post, sign):
        challenge = encoded_hex(secrets.token_bytes(32))
        signature = await sign(encoded_hex(_signing_bytes(self._policy,self._grant["agentPublicKey"],challenge)))
        result = await post("/v1/agent/context", {"schema":REQUEST_SCHEMA,"chain_id":421614,
                            "settlement":_NATIVE_SETTLEMENT,"agent_public_key":self._grant["agentPublicKey"],
                            "challenge":challenge,"signature":signature})
        exact(result,("schema","status","challenge","context","public_grant","next_nonce",
                      "proof_backed","l1_finality_verified","public_release_go"))
        if (result["schema"] != RESPONSE_SCHEMA or result["status"] != "canonical_prepared_agent_context_not_final"
                or result["challenge"] != challenge or grant_copy(result["public_grant"],self._policy,self._crypto) != self._grant
                or any(result[key] is not False for key in ("proof_backed","l1_finality_verified","public_release_go"))):
            fail("response_invalid")
        context = context_copy(result["context"],self._policy,self._grant)
        nonce = decimal(result["next_nonce"],safe=True)
        now = int(context["protocol_time_ms"])
        if context["session_scope"] != _scope(self._policy,self._grant) or now < self._time_floor or nonce < self._nonce_floor:
            fail("context_changed")
        self._time_floor,self._nonce_floor = now,nonce
        return {"context":copy.deepcopy(context),"next_nonce":result["next_nonce"],"public_grant":copy.deepcopy(self._grant)}

    async def snapshot(self, *, timeout_ms=15000):
        async def work(post,sign,check):
            return await self._context(post,sign)
        return await self._operation(timeout_ms,work)

    async def _recover(self, post):
        original = self._original()
        if original is None:
            fail("no_pending_request")
        request = original["request"]
        result = await post("/v1/agent/recovery",{"signed_request":request})
        exact(result,("schema","status","request_sha256","step","retry_authorized",
                      "proof_backed","l1_finality_verified","public_release_go"))
        if (result["schema"] != "fairvenue-v1-agent-recovery-v1"
                or result["request_sha256"] != encoded_hex(hashlib.sha256(_wire(request)).digest())
                or integer(result["step"],511) != request["intent"]["expected"]["head"]["count"]
                or result["status"] not in ("accepted","rejected","unresolved","bound_incomplete")
                or any(result[key] is not False for key in ("retry_authorized","proof_backed","l1_finality_verified","public_release_go"))):
            fail("response_invalid")
        if result["status"] in ("accepted","rejected"):
            revision,highest,stored = self._custody.read()
            if stored != original:
                fail("context_changed")
            self._custody.change(revision,max(highest,decimal(request["intent"]["nonce"],safe=True)),None)
        return copy.deepcopy(result)

    async def recover_original(self, *, timeout_ms=15000):
        """Exact original lookup only, even after expiry/revocation/new opening.

        unresolved/bound_incomplete retain it. This method never sends admit.
        """
        async def work(post,sign,check):
            return await self._recover(post)
        return await self._operation(timeout_ms,work)

    async def run(self, input, *, timeout_ms=60000):
        """One explicitly authorized native action; no automatic resend/reprice."""
        exact(input,("nonce","expiresAtMs","action"))
        chosen = {"nonce":input["nonce"],"expiresAtMs":input["expiresAtMs"],"action":action_copy(input["action"])[0]}
        decimal(chosen["nonce"],safe=True); decimal(chosen["expiresAtMs"],safe=True)
        async def work(post,sign,check):
            if self._original() is not None:
                fail("outcome_unknown")
            revision,highest,_ = self._custody.read()
            if int(chosen["nonce"]) <= highest:
                fail("nonce_reused")
            original = await self._context(post,sign)
            if chosen["nonce"] != original["next_nonce"]:
                fail("nonce_reused")
            signables = build(self._policy,self._grant,original["context"],chosen,self._crypto)
            request = {"intent":signables["intent"],"intent_signature":await sign(signables["intent_signing_bytes"])}
            # Pure existing validator does not enter AgentClient's closed run.
            AgentClient._AgentClient__preparation(await post("/v1/agent/prepare",request),signables)
            request["ordinary_signature"] = await sign(signables["ordinary_signing_hash"])
            request["generation_signature"] = await sign(signables["generation_signing_bytes"])
            fresh = await self._context(post,sign)
            AgentClient._AgentClient__same(fresh["context"],original["context"])
            if fresh["next_nonce"] != chosen["nonce"]:
                fail("nonce_reused")
            check()
            pending = {"schema":"fairvenue-native-testnet-public-original-v1","request":request,
                       "protocol_time_ms":original["context"]["protocol_time_ms"]}
            self._custody.change(revision,highest,pending)
            self._original()  # Read back and locally authenticate before send.
            ack = AgentClient._AgentClient__ack(await post("/v1/agent/admit",request),signables["intent"]["expected"],self._policy)
            recovery = await self._recover(post)
            if recovery["status"] not in ("accepted","rejected"):
                fail("outcome_unknown")
            return {"ack":ack,"recovery":recovery}
        return await self._operation(timeout_ms,work)
