"""Source-closed agent admission, exact local signing and retained unknown-outcome retry."""

from __future__ import annotations

import asyncio
import copy
import inspect
import json
import time
import weakref
from dataclasses import dataclass, field

from .crypto import CryptoPrimitives
from .protocol import (
    AgentError, action_copy, build, context_copy, decimal, encoded_hex, exact,
    fail, grant_copy, integer, policy_copy, prefix_copy,
)


def _json(value):
    def pairs(items):
        result = {}
        for key, item in items:
            if key in result:
                fail("response_invalid")
            result[key] = item
        return result

    def reject(_):
        fail("response_invalid")

    if type(value) is not bytes or len(value) > 16384:
        fail("response_invalid")
    try:
        return json.loads(value.decode("utf-8", errors="strict"), object_pairs_hook=pairs,
                          parse_float=reject, parse_constant=reject)
    except Exception:
        raise AgentError("response_invalid") from None


def _snapshot(value):
    """Small public-only configuration snapshot; no arbitrary objects or floating-point values."""
    def visit(item, depth=0):
        if depth > 10:
            fail()
        if item is None or type(item) in (bool, int, str):
            if type(item) is int:
                integer(item)
            if type(item) is str and len(item) > 256:
                fail()
            return
        if type(item) is list and len(item) <= 32:
            for child in item:
                visit(child, depth + 1)
            return
        if type(item) is dict and len(item) <= 16:
            for key, child in item.items():
                if type(key) is not str or len(key) > 64:
                    fail()
                visit(child, depth + 1)
            return
        fail()
    visit(value)
    data = json.dumps(value, separators=(",", ":"), ensure_ascii=False).encode()
    if len(data) > 16384:
        fail()
    return data


@dataclass
class _State:
    identity: bytes
    busy: bool = False
    pending: object = None
    highest: int = 0
    inflight: set = field(default_factory=set)
    loop: object = None


_SIGNERS = weakref.WeakKeyDictionary()


class AgentClient:
    """Same signer-object clients share a fence; restart is NOT durable recovery."""

    def __init__(self, *, policy, grant, signer, current_context, transport):
        # No primitive imports, network or signatures; closed run/retry work with deps absent.
        self.__policy_bytes = _snapshot(policy)
        self.__grant_bytes = _snapshot(grant)
        if (not callable(getattr(signer, "sign_bytes", None)) or not callable(current_context)
                or not callable(getattr(transport, "post", None))):
            fail()
        identity = self.__policy_bytes + b"\0" + self.__grant_bytes
        try:
            state = _SIGNERS.get(signer)
            if state is None:
                state = _State(identity)
                _SIGNERS[signer] = state
            elif state.identity != identity:
                fail()
        except TypeError:
            fail()
        self.__signer = signer
        self.__sign_bytes = signer.sign_bytes
        self.__public_key = getattr(signer, "public_key", None)
        self.__context = current_context
        self.__post = transport.post
        self.__state = state
        self.__crypto = None

    def has_pending_request(self):
        return self.__state.pending is not None

    def __pins(self):
        if self.__crypto is None:
            self.__crypto = CryptoPrimitives()
        policy = policy_copy(_json(self.__policy_bytes), self.__crypto)
        grant = grant_copy(_json(self.__grant_bytes), policy, self.__crypto)
        if self.__public_key != grant["agentPublicKey"]:
            fail()
        return policy, grant

    async def __operation(self, timeout_ms, work):
        state = self.__state
        if state.busy or state.inflight:
            fail("busy")
        if type(timeout_ms) is not int or not 1 <= timeout_ms <= 60000:
            fail()
        loop = asyncio.get_running_loop()
        if state.loop is not None and state.loop is not loop:
            fail("context_changed")
        state.loop = loop
        deadline = time.monotonic() + timeout_ms / 1000
        state.busy = True
        policy, grant = None, None

        def check():
            if time.monotonic() >= deadline:
                fail("deadline")

        async def bounded(callback):
            check()

            async def invoke():
                check()
                result = callback()
                if not inspect.isawaitable(result):
                    fail()
                return await result

            task = asyncio.create_task(invoke())
            state.inflight.add(task)

            def done(completed):
                state.inflight.discard(completed)
                # Retrieve without printing callback exceptions (may contain secrets).
                if not completed.cancelled():
                    completed.exception()

            task.add_done_callback(done)
            completed, _ = await asyncio.wait({task}, timeout=min(15, deadline - time.monotonic()))
            if not completed:
                # DO NOT cancel and release the fence while a network/sign callback still runs.
                fail("deadline")
            result = task.result()
            check()
            return result

        async def context():
            return context_copy(await bounded(self.__context), policy, grant)

        async def post(path, body):
            if type(body) is not bytes or len(body) > 8192:
                fail()
            response = await bounded(lambda: self.__post(path, body, min(15, deadline - time.monotonic())))
            return _json(response)

        try:
            policy, grant = self.__pins()
            return await work(policy, grant, context, post, bounded, check)
        except asyncio.CancelledError:
            # Retain pending body and any live callback lease. Caller cancellation is never an ACK.
            raise
        except Exception as error:
            code = "outcome_unknown" if state.pending else error.code if isinstance(error, AgentError) else "unavailable"
            raise AgentError(code) from None
        finally:
            state.busy = False

    @staticmethod
    def __same(current, original, *, fresh=True):
        if (current["session_scope"] != original["session_scope"] or current["owner"] != original["owner"]
                or int(current["protocol_time_ms"]) < int(original["protocol_time_ms"])
                or current["prefix"]["domain"] != original["prefix"]["domain"]
                or current["prefix"]["opening"] != original["prefix"]["opening"]
                or (fresh and current["prefix"]["head"] != original["prefix"]["head"])
                or (not fresh and (
                    current["prefix"]["head"]["count"] < original["prefix"]["head"]["count"]
                    or (current["prefix"]["head"]["count"] == original["prefix"]["head"]["count"]
                        and current["prefix"]["head"] != original["prefix"]["head"])
                ))):
            fail("context_changed")

    @staticmethod
    def __preparation(value, signables):
        exact(value, ("status", "prefix", "ordinary_signing_hash", "generation_signing_bytes",
                      "proof_backed", "l1_finality_verified", "public_release_go"))
        if (value["status"] != "unsigned_agent_command"
                or prefix_copy(value["prefix"]) != signables["intent"]["expected"]
                or value["ordinary_signing_hash"] != signables["ordinary_signing_hash"]
                or value["generation_signing_bytes"] != signables["generation_signing_bytes"]
                or any(value[key] is not False for key in ("proof_backed", "l1_finality_verified", "public_release_go"))):
            fail("response_invalid")

    @staticmethod
    def __ack(value, expected, policy):
        keys = ("status", "batch", "generation", "step", "duplicate", "outcome", "roster", "head",
                "proof_backed", "l1_finality_verified", "public_release_go")
        exact(value, (*keys, "anchor_receipt") if "anchor_receipt" in value else keys)
        roster = value["roster"]
        if (value["status"] != "soft_ack_prepared_not_final"
                or integer(value["batch"]) != expected["opening"]["batch"]
                or integer(value["generation"]) != expected["opening"]["generation"]
                or integer(value["step"]) != expected["head"]["count"]
                or type(value["duplicate"]) is not bool or value["outcome"] not in ("trade", "rejected_trade")
                or type(roster) is not list or len(roster) != 2
                or any(type(key) is not int for key in roster) or roster[0] >= roster[1]
                or any(not any(key["gateway_id"] == item for key in policy["gateways"]) for item in roster)
                or any(value[key] is not False for key in ("proof_backed", "l1_finality_verified", "public_release_go"))):
            fail("response_invalid")
        head = prefix_copy({**expected, "head": value["head"]}, retry=True)["head"]
        if head["count"] <= expected["head"]["count"]:
            fail("response_invalid")
        # Opaque anchor receipt is dropped, never promoted to a verification claim.
        return {**{key: copy.deepcopy(value[key]) for key in keys}, "head": head}

    async def __send_pending(self, policy, grant, context, post, bounded, check):
        del grant, bounded, check
        pending = self.__state.pending
        if pending is None:
            fail("no_pending_request")
        before = await context()
        self.__same(before, pending["context"], fresh=False)
        ack = self.__ack(await post("/v1/agent/admit", pending["body"]), pending["context"]["prefix"], policy)
        after = await context()
        self.__same(after, pending["context"], fresh=False)
        if (after["prefix"]["head"]["count"] < ack["head"]["count"]
                or (after["prefix"]["head"]["count"] == ack["head"]["count"]
                    and after["prefix"]["head"] != ack["head"])):
            fail("response_invalid")
        self.__state.highest = int(pending["nonce"])
        self.__state.pending = None
        return ack

    async def run(self, input, *, timeout_ms=60000):
        # SOURCE ACTIVATION GATE: literal, not an attribute, environment or caller flag.
        if not False:
            fail("activation_closed")
        if self.__state.pending:
            fail("outcome_unknown")
        exact(input, ("nonce", "expiresAtMs", "action"))
        chosen = {"nonce": input["nonce"], "expiresAtMs": input["expiresAtMs"],
                  "action": action_copy(input["action"])[0]}
        if decimal(chosen["nonce"], safe=True) <= self.__state.highest:
            fail("nonce_reused")
        decimal(chosen["expiresAtMs"], safe=True)

        async def work(policy, grant, context, post, bounded, check):
            original = await context()
            signables = build(policy, grant, original, chosen, self.__crypto)

            async def sign(data):
                message = bytes.fromhex(data[2:])
                signature = await bounded(lambda: self.__sign_bytes(message))
                if (type(signature) is not bytes or len(signature) != 64
                        or not self.__crypto.verify(encoded_hex(signature), message, grant["agentPublicKey"])):
                    fail("signature_invalid")
                self.__same(await context(), original)
                return encoded_hex(signature)

            request = {"intent": signables["intent"],
                       "intent_signature": await sign(signables["intent_signing_bytes"])}
            body = json.dumps(request, separators=(",", ":")).encode()
            self.__preparation(await post("/v1/agent/prepare", body), signables)
            self.__same(await context(), original)
            request["ordinary_signature"] = await sign(signables["ordinary_signing_hash"])
            request["generation_signature"] = await sign(signables["generation_signing_bytes"])
            self.__same(await context(), original)
            check()
            self.__state.pending = {
                "body": json.dumps(request, separators=(",", ":")).encode(),
                "context": original, "nonce": chosen["nonce"],
            }
            return await self.__send_pending(policy, grant, context, post, bounded, check)

        return await self.__operation(timeout_ms, work)

    async def retry_admission(self, *, timeout_ms=60000):
        # SOURCE ACTIVATION GATE: literal, not an attribute, environment or caller flag.
        if not False:
            fail("activation_closed")
        return await self.__operation(timeout_ms, self.__send_pending)
