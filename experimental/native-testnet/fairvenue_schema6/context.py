"""Source-closed, agent-authenticated read-only native bootstrap. No cookie or nonce authority."""

from __future__ import annotations

import asyncio
import copy
import hashlib
import inspect
import json
import secrets
import time

from .client import _json, _snapshot
from .crypto import CryptoPrimitives
from .protocol import (
    AgentError, context_copy, decimal, encoded_hex, exact, fail, grant_copy,
    hex_value, policy_copy, uint,
)

REQUEST_SCHEMA = "fairvenue-v1-agent-context-request-v1"
RESPONSE_SCHEMA = "fairvenue-v1-agent-context-v1"
SIGNING_DOMAIN = b"FairVenue.V1.AgentContextRequest.v1\0"
SCOPE_DOMAIN = b"FairVenue.V1.AgentContextScope.v1\0"


def _signing_bytes(policy, agent_public_key, challenge):
    return (SIGNING_DOMAIN + uint(policy["domain"]["chain_id"])
            + bytes(policy["domain"]["settlement"])
            + bytes.fromhex(hex_value(agent_public_key)[2:])
            + bytes.fromhex(hex_value(challenge)[2:]))


def build_agent_context_signing_bytes(*, policy, agent_public_key, challenge):
    """Pure fixed-width wire reconstruction. No signature, HTTP or authority."""
    crypto = CryptoPrimitives()
    pinned = policy_copy(policy, crypto)
    if not crypto.strong(agent_public_key):
        fail("signature_invalid")
    return _signing_bytes(pinned, agent_public_key, challenge)


def _scope(policy, grant):
    fields = ("delegationNonce", "issuedAtMs", "expiresAtMs", "maxExpiryMs")
    return hashlib.sha256(
        SCOPE_DOMAIN + uint(policy["domain"]["chain_id"])
        + bytes(policy["domain"]["settlement"])
        + bytes.fromhex(grant["owner"][2:]) + bytes.fromhex(grant["account"][2:])
        + bytes.fromhex(grant["agentPublicKey"][2:]) + uint(grant["permissions"], 1)
        + b"".join(uint(int(grant[key])) for key in fields)
        + bytes.fromhex(policy["commitment"][2:])
    ).hexdigest()


class BotContextProvider:
    """Concrete native read hook; installed grant/policy cannot come from its response.

    Reads consume no trading nonce and create no account, key or collateral.
    A fresh challenge binds the response to this exact read, not a persistent
    browser/bot identifier. Repeated captured reads confer no trading authority.
    """

    def __init__(self, *, policy, grant, signer, transport):
        self.__policy = _snapshot(policy)
        self.__grant = _snapshot(grant)
        if (not callable(getattr(signer, "sign_bytes", None))
                or not callable(getattr(transport, "post", None))):
            fail()
        self.__sign = signer.sign_bytes
        self.__public_key = getattr(signer, "public_key", None)
        self.__post = transport.post
        self.__crypto = None
        self.__busy = False
        self.__inflight = set()
        self.__loop = None
        self.__time_floor = 0
        self.__nonce_floor = 0

    async def current_context(self):
        """The exact async hook accepted by AgentClient; no Google/session dependency."""
        return (await self.snapshot())["context"]

    async def snapshot(self, *, timeout_ms=15000):
        # Source gate FIRST: no dependency load, challenge, signing, context or I/O.
        if not False:
            fail("activation_closed")
        if self.__busy or self.__inflight:
            fail("busy")
        if type(timeout_ms) is not int or not 1 <= timeout_ms <= 15000:
            fail()
        loop = asyncio.get_running_loop()
        if self.__loop is not None and self.__loop is not loop:
            fail("context_changed")
        self.__loop = loop
        self.__busy = True
        deadline = time.monotonic() + timeout_ms / 1000

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
            self.__inflight.add(task)

            def done(completed):
                self.__inflight.discard(completed)
                if not completed.cancelled():
                    completed.exception()  # Retrieve only; never print raw exceptions.

            task.add_done_callback(done)
            completed, _ = await asyncio.wait({task}, timeout=max(0, deadline - time.monotonic()))
            if not completed:
                # Do not release a live signing/HTTP callback permit on timeout.
                fail("deadline")
            result = task.result()
            check()
            return result

        try:
            if self.__crypto is None:
                self.__crypto = CryptoPrimitives()
            policy = policy_copy(_json(self.__policy), self.__crypto)
            grant = grant_copy(_json(self.__grant), policy, self.__crypto)
            if self.__public_key != grant["agentPublicKey"]:
                fail("signature_invalid")
            # Exactly one local CSPRNG read; no challenge or signature is retained.
            challenge = encoded_hex(secrets.token_bytes(32))
            message = _signing_bytes(policy, self.__public_key, challenge)
            signature = await bounded(lambda: self.__sign(message))
            if (type(signature) is not bytes or len(signature) != 64
                    or not self.__crypto.verify(encoded_hex(signature), message, self.__public_key)):
                fail("signature_invalid")
            request = {
                "schema": REQUEST_SCHEMA, "chain_id": 421614,
                "settlement": encoded_hex(policy["domain"]["settlement"]),
                "agent_public_key": self.__public_key, "challenge": challenge,
                "signature": encoded_hex(signature),
            }
            body = json.dumps(request, separators=(",", ":")).encode()
            result = _json(await bounded(lambda: self.__post(
                "/v1/agent/context", body, min(15, deadline - time.monotonic())
            )))
            exact(result, ("schema", "status", "challenge", "context", "public_grant",
                           "next_nonce", "proof_backed", "l1_finality_verified", "public_release_go"))
            if (result["schema"] != RESPONSE_SCHEMA
                    or result["status"] != "canonical_prepared_agent_context_not_final"
                    or result["challenge"] != challenge
                    or any(result[key] is not False for key in (
                        "proof_backed", "l1_finality_verified", "public_release_go"))):
                fail("response_invalid")
            actual = grant_copy(result["public_grant"], policy, self.__crypto)
            if actual != grant:
                fail("context_changed")
            context = context_copy(result["context"], policy, grant)
            next_nonce = decimal(result["next_nonce"], safe=True)
            now = int(context["protocol_time_ms"])
            if (context["session_scope"] != _scope(policy, grant)
                    or now < self.__time_floor or next_nonce < self.__nonce_floor):
                fail("context_changed")
            check()
            self.__time_floor, self.__nonce_floor = now, next_nonce
            # Caller receives a fresh public-only copy, not mutable installed pins.
            return {"context": copy.deepcopy(context), "public_grant": copy.deepcopy(grant),
                    "next_nonce": result["next_nonce"]}
        except asyncio.CancelledError:
            raise
        except Exception as error:
            raise AgentError(error.code if isinstance(error, AgentError) else "unavailable") from None
        finally:
            self.__busy = False
