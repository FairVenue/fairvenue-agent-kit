"""Exact TS2 public wire codec. No key generation, network, account or V4 conversion.

Byte for byte the reviewed TS2 codec (``trade-settlement/client/codec.mjs``) and
the Rust adapter: Ed25519 signing messages, order ids, the request digest and
the public reply shapes. Checked against the shared JS/Rust vectors. The
owner's EIP-712 delegation signature is verified by the server. This package
has no secp256k1 dependency, so it checks only that grant's exact shape, its
pins and the agent key it names, and that the server returns the same grant.
"""

from __future__ import annotations

import hashlib
import json
import re

from .protocol import AgentError, fail

CHAIN_ID = 421614
SETTLEMENT = "0x3beb98f3ab27d3ac9595e28a3ca03e016fb2745b"
CONFIG_HASH = "0x6a72cb1998229bcd0d5ce8ea8bbeb0d6c294ee51a385f08208fe3439b58d8d91"
#: The only TS2 deployment this package signs for. A new deployment needs a new release.
PINS = {"chain_id": CHAIN_ID, "settlement": SETTLEMENT, "config_hash": CONFIG_HASH}

KEY_SCHEMA = "fairvenue-ts2-testnet-agent-v1"
#: The retired V4 bot key format. It is refused by name, never adapted.
V4_KEY_SCHEMA = "fairvenue-native-testnet-agent-v1"
REQUEST_SCHEMA = "fairvenue-ts2-agent-request-v1"
MATERIAL_SCHEMA = "fairvenue-ts2-delegation-material-v1"
CONTEXT_REQUEST_SCHEMA = "fairvenue-v1-agent-context-request-v1"
CONTEXT_SCHEMA = "fairvenue-ts2-agent-context-v1"
ACK_SCHEMA = "fairvenue-ts2-agent-ack-v1"
RECOVERY_SCHEMA = "fairvenue-ts2-agent-recovery-v1"
MARKET_SCHEMA = "fairvenue-ts2-public-market-v1"

#: Delegation rights (fairvenue-protocol ``delegation.rs``).
TRADE, CANCEL, MODIFY, DEAD_MAN_SWITCH = 1, 2, 4, 8
#: Definitive admit refusal of an agent request: reason -> its only ``retry``.
#: HTTP 409, or 429 for ``rate_limited``. Nothing was journaled.
NOT_PLACED = {"invalid_request": "resign", "rejected": "resign",
              "admission_closed": "resign", "rate_limited": "resign"}
#: Exact-original lookup status on the wire -> the public outcome name.
#: ``unknown`` is never evidence that the request was not sent.
RECOVERY = {"operator_journaled": "accepted", "not_placed": "rejected",
            "not_settled": "not_settled", "unknown": "unresolved"}
#: Outcomes that retire exactly the looked-up original.
TERMINAL = ("accepted", "rejected", "not_settled")
SECURITY = ("set_dms", "set_fee_mode", "set_leverage")

U64 = (1 << 64) - 1
SAFE = (1 << 53) - 1
_ZERO = "0x" + "00" * 32
_FLAGS = (("proof_backed", "l1_finality_verified", "public_release_go"),
          ("proofVerified", "publicFinality", "publicReleaseGo"))
_DOMAIN = ("chain_id", "settlement", "config_hash")
_ORDER = ("type", "delegation_nonce", "client_nonce", "side", "kind", "time_in_force",
          "post_only", "reduce_only", "fee_mode", "limit_price_ticks", "quantity_lots",
          "expires_at_ms")
_SECURITY_FIELDS = ("type", "delegation_nonce", "command_nonce", "expires_at_ms")
_REQUEST = ("schema", *_DOMAIN, "account_key", "agent_public_key", "signature", "action")
_MATERIAL = ("schema", *_DOMAIN, "owner", "account_key", "agent_public_key", "permissions",
             "nonce", "issued_at_ms", "expires_at_ms", "max_expiry_ms", "can_withdraw")
_CONTEXT = (*_DOMAIN, "account_key", "batch_number", "lineage", "protocol_time_ms",
            "fee_mode", "leverage", "admission")
_DECIMAL = re.compile(r"(0|[1-9][0-9]*)(\.[0-9]+)?")
_REASON = re.compile(r"[a-z][a-z0-9_]{0,47}")


def _exact(value, keys):
    if type(value) is not dict or set(value) != set(keys) or len(value) != len(keys):
        fail()


def uint(value, *, nonzero=False):
    """An exact u64 decimal string, the only wire form of large integers."""
    if (type(value) is not str or not 0 < len(value) <= 20
            or re.fullmatch(r"0|[1-9][0-9]*", value) is None or int(value) > U64
            or (nonzero and value == "0")):
        fail()
    return value


def fixed(value, size, *, nonzero=False):
    """Exactly ``size`` bytes as lowercase ``0x`` hex."""
    if type(value) is not str or re.fullmatch("0x[0-9a-f]{%d}" % (size * 2), value) is None:
        fail()
    raw = bytes.fromhex(value[2:])
    if nonzero and not any(raw):
        fail()
    return raw


def _code(value, maximum):
    if type(value) is not int or not 0 <= value <= maximum:
        fail()
    return bytes([value])


def _flag(value):
    if type(value) is not bool:
        fail()
    return bytes([int(value)])


def _u64(value):
    return int(uint(value)).to_bytes(8, "big")


def _keccak(crypto, data):
    return bytes.fromhex(crypto.digest(data)[2:])


def canonical(value):
    """RFC 8785 JCS of this codec's public values only: objects, arrays, printable
    ASCII strings, booleans, null and integers up to 2^53 - 1."""
    def visit(item, depth):
        if depth > 8:
            fail()
        if item is None or type(item) is bool:
            return
        if type(item) is int:
            if not 0 <= item <= SAFE:
                fail()
            return
        if type(item) is str:
            if re.fullmatch(r"[\x20-\x7e]*", item) is None:
                fail()
            return
        if type(item) is list:
            for child in item:
                visit(child, depth + 1)
            return
        if type(item) is dict:
            for key, child in item.items():
                if type(key) is not str or re.fullmatch(r"[\x20-\x7e]+", key) is None:
                    fail()
                visit(child, depth + 1)
            return
        fail()

    visit(value, 0)
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def parse_json(data, limit=16384):
    """One bounded JSON reply: no duplicate keys, binary floats or constants."""
    def pairs(items):
        result = {}
        for key, item in items:
            if key in result:
                fail("response_invalid")
            result[key] = item
        return result

    def reject(_):
        fail("response_invalid")

    if type(data) is not bytes or len(data) > limit:
        fail("response_invalid")
    try:
        return json.loads(data.decode("utf-8", errors="strict"), object_pairs_hook=pairs,
                          parse_float=reject, parse_constant=reject)
    except Exception:
        raise AgentError("response_invalid") from None


def deployment(value):
    """Exact ``{chain_id, settlement, config_hash}``; chain 31337 only for vectors."""
    _exact(value, _DOMAIN)
    if type(value["chain_id"]) is not int or value["chain_id"] not in (31337, CHAIN_ID):
        fail()
    fixed(value["settlement"], 20, nonzero=True)
    fixed(value["config_hash"], 32, nonzero=True)
    return {key: value[key] for key in _DOMAIN}


def pinned(value):
    """The installed TS2 deployment only: chain 421614 and its exact pins."""
    pins = deployment(value)
    if pins != PINS:
        fail()
    return pins


def account_number(account_key):
    """The u64 of a public account word (``0x`` 24 zero bytes then the u64)."""
    raw = fixed(account_key, 32, nonzero=True)
    if any(raw[:24]):
        fail()
    return int.from_bytes(raw[24:], "big")


def action_copy(value):
    """One exact TS2 agent action, as the reviewed codec accepts it."""
    kind = value.get("type") if type(value) is dict else None
    if kind == "order":
        _exact(value, _ORDER)
        uint(value["delegation_nonce"], nonzero=True)
        uint(value["client_nonce"])
        uint(value["limit_price_ticks"], nonzero=True)
        uint(value["quantity_lots"], nonzero=True)
        uint(value["expires_at_ms"], nonzero=True)
        _code(value["side"], 1)
        _code(value["kind"], 1)
        _code(value["time_in_force"], 2)
        _code(value["fee_mode"], 1)
        _flag(value["post_only"])
        _flag(value["reduce_only"])
        if ((value["kind"] == 1 and (value["time_in_force"] == 0 or value["post_only"]))
                or (value["post_only"] and value["time_in_force"] != 0)):
            fail()
    elif kind == "cancel":
        _exact(value, ("type", "order_id"))
        fixed(value["order_id"], 32, nonzero=True)
    elif kind == "kill_all":
        _exact(value, ("type", "client_nonce"))
        uint(value["client_nonce"])
    elif kind in SECURITY:
        last = {"set_dms": "deadline_ms", "set_fee_mode": "fee_mode",
                "set_leverage": "leverage"}[kind]
        _exact(value, (*_SECURITY_FIELDS, last))
        uint(value["delegation_nonce"], nonzero=True)
        uint(value["command_nonce"], nonzero=True)
        uint(value["expires_at_ms"], nonzero=True)
        if kind == "set_dms":
            uint(value["deadline_ms"])
        elif kind == "set_fee_mode":
            _code(value["fee_mode"], 1)
        elif int(uint(value["leverage"], nonzero=True)) > 100:
            fail()
    else:
        fail()
    return json.loads(canonical(value))


def signing_message(pins, account_key, agent_public_key, action, crypto):
    """``(exact native bytes one Ed25519 signature covers, order id or None)``.

    Order: ``"FV_TS2_ORDER_SIG\\0" || order_id``. Cancel and kill-all bind the
    config hash and the u64 account. Security kinds 0 set_dms, 1 set_fee_mode
    and 2 set_leverage sign ``"FV_TS2_SECURITY_SIG\\0" || keccak(...)``.
    """
    pins = deployment(pins)
    action = action_copy(action)
    cfg = fixed(pins["config_hash"], 32)
    account = account_number(account_key).to_bytes(8, "big")
    agent = fixed(agent_public_key, 32, nonzero=True)
    kind = action["type"]
    if kind == "order":
        core = _keccak(crypto, b"FairVenue.ts2.intent-core.v1\0" + cfg + account + agent
                       + _u64(action["delegation_nonce"]) + _u64(action["client_nonce"])
                       + _code(action["side"], 1) + _code(action["kind"], 1)
                       + _code(action["time_in_force"], 2) + _flag(action["post_only"])
                       + _flag(action["reduce_only"]) + _code(action["fee_mode"], 1)
                       + _u64(action["limit_price_ticks"]) + _u64(action["quantity_lots"]))
        order_id = _keccak(crypto, b"FairVenue.ts2.order.v1\0" + core
                           + _u64(action["expires_at_ms"]))
        return b"FV_TS2_ORDER_SIG\0" + order_id, "0x" + order_id.hex()
    if kind == "cancel":
        return b"FV_TS2_CANCEL_SIG\0" + cfg + account + fixed(action["order_id"], 32), None
    if kind == "kill_all":
        return b"FV_TS2_KILLALL_SIG\0" + cfg + account + _u64(action["client_nonce"]), None
    code, value = {"set_dms": (0, action.get("deadline_ms")),
                   "set_fee_mode": (1, str(action.get("fee_mode"))),
                   "set_leverage": (2, action.get("leverage"))}[kind]
    digest = _keccak(crypto, b"FairVenue.ts2.security.v1\0" + cfg + account + agent
                     + _u64(action["delegation_nonce"]) + _u64(action["command_nonce"])
                     + _u64(action["expires_at_ms"]) + bytes([code]) + _u64(value))
    return b"FV_TS2_SECURITY_SIG\0" + digest, None


def request_copy(value, pins, crypto):
    """A complete signed request, its signature verified against its own bytes."""
    _exact(value, _REQUEST)
    if value["schema"] != REQUEST_SCHEMA:
        fail()
    own = deployment({key: value[key] for key in _DOMAIN})
    if pins is not None and own != deployment(pins):
        fail()
    message, _ = signing_message(own, value["account_key"], value["agent_public_key"],
                                 value["action"], crypto)
    fixed(value["signature"], 64)
    if not crypto.verify(value["signature"], message, value["agent_public_key"]):
        fail("signature_invalid")
    text = canonical(value)
    if len(text.encode()) > 8192:
        fail()
    return json.loads(text)


def request_sha256(value):
    """The server's request identity: SHA-256 of the domain-tagged JCS request."""
    return "0x" + hashlib.sha256(b"FairVenue.ts2.agent-request.v1\0"
                                 + canonical(value).encode()).hexdigest()


def material_copy(value, pins=None):
    """Exact owner-signed TS2 delegation material: widths, rights, no withdrawal."""
    _exact(value, _MATERIAL)
    own = deployment({key: value[key] for key in _DOMAIN})
    if value["schema"] != MATERIAL_SCHEMA or (pins is not None and own != deployment(pins)):
        fail()
    fixed(value["owner"], 20, nonzero=True)
    account_number(value["account_key"])
    fixed(value["agent_public_key"], 32, nonzero=True)
    _code(value["permissions"], 15)
    if (not value["permissions"] & (TRADE | CANCEL | DEAD_MAN_SWITCH)
            or value["can_withdraw"] is not False):
        fail()
    uint(value["nonce"], nonzero=True)
    issued = int(uint(value["issued_at_ms"]))
    expires = int(uint(value["expires_at_ms"], nonzero=True))
    uint(value["max_expiry_ms"], nonzero=True)
    if (expires <= issued or value["max_expiry_ms"] != value["expires_at_ms"]
            or expires - issued > 2_592_000_000 or expires > SAFE):
        fail()
    return json.loads(canonical(value))


def grant_copy(value, pins=None):
    """The public grant of one agent key: ``{material, signature}``. Never a key.

    The 65-byte owner signature is not recovered here. The server verifies it
    and the client requires the server to return this exact grant.
    """
    _exact(value, ("material", "signature"))
    material = material_copy(value["material"], pins)
    fixed(value["signature"], 65, nonzero=True)
    return {"material": material, "signature": value["signature"]}


def context_signing_bytes(pins, agent_public_key, challenge):
    """``FairVenue.V1.AgentContextRequest.v1\\0 || u64be chain || settlement || agent || challenge``."""
    pins = deployment(pins)
    return (b"FairVenue.V1.AgentContextRequest.v1\0" + pins["chain_id"].to_bytes(8, "big")
            + fixed(pins["settlement"], 20) + fixed(agent_public_key, 32, nonzero=True)
            + fixed(challenge, 32, nonzero=True))


def flag_keys(value):
    """Exactly one of the two public flag triples, every flag false. Returns its keys."""
    if type(value) is not dict:
        fail("response_invalid")
    for triple, other in ((_FLAGS[1], _FLAGS[0]), (_FLAGS[0], _FLAGS[1])):
        if any(key in value for key in triple):
            if (any(value.get(key) is not False for key in triple)
                    or any(key in value for key in other)):
                fail("response_invalid")
            return triple
    fail("response_invalid")


def admission(value):
    """Exact ``{open, reasons}``: open exactly when no reason word is given."""
    _exact(value, ("open", "reasons"))
    reasons = value["reasons"]
    if (type(value["open"]) is not bool or type(reasons) is not list or len(reasons) > 8
            or any(type(item) is not str or _REASON.fullmatch(item) is None for item in reasons)
            or len(set(reasons)) != len(reasons) or value["open"] != (not reasons)):
        fail()
    return {"open": value["open"], "reasons": list(reasons)}


def context_copy(value, pins, grant, challenge):
    """One signed agent context reply for exactly this key, grant and challenge.

    Returns ``(context, next_command_nonce)``. Never admission authority.
    """
    try:
        _exact(value, ("schema", "status", "challenge", "context", "public_grant",
                       "next_command_nonce", *flag_keys(value)))
        context = value["context"]
        # The manifest flag for fee mode and leverage commands. Older replies omit it.
        optional = ()
        if type(context) is dict and "account_settings_enabled" in context:
            optional = ("account_settings_enabled",)
        _exact(context, (*_CONTEXT, *optional))
        material = grant["material"]
        if (value["schema"] != CONTEXT_SCHEMA
                or value["status"] != "operator_native_agent_context_not_final"
                or value["challenge"] != challenge
                or deployment({key: context[key] for key in _DOMAIN}) != deployment(pins)
                or context["account_key"] != material["account_key"]
                or type(context["fee_mode"]) is not int or context["fee_mode"] not in (0, 1)
                or type(context["leverage"]) is not str
                or re.fullmatch(r"[1-9][0-9]{0,2}", context["leverage"]) is None
                or (optional and type(context["account_settings_enabled"]) is not bool)
                or canonical(grant_copy(value["public_grant"], pins)) != canonical(grant)):
            fail()
        uint(context["batch_number"], nonzero=True)
        uint(context["lineage"])
        if int(uint(context["protocol_time_ms"])) > SAFE:
            fail()
        uint(value["next_command_nonce"], nonzero=True)
        result = {key: context[key] for key in _CONTEXT if key != "admission"}
        result["admission"] = admission(context["admission"])
        result["account_settings_enabled"] = (
            context["account_settings_enabled"] if optional else None)
        return result, value["next_command_nonce"]
    except AgentError:
        raise AgentError("response_invalid") from None


def ack_copy(value, request, digest, order_id):
    """The operator-journal ACK of exactly ``request``. Not a fill, proof or finality.

    Receipts and certificates are not interpreted. Any mismatch is an unknown outcome.
    """
    try:
        flag_keys(value)
        kind = request["action"]["type"]
        security = kind in SECURITY
        if (value.get("schema") != ACK_SCHEMA or value.get("status") != "operator_journaled"
                or value.get("request_sha256") != digest
                or value.get("config_hash") != request["config_hash"]
                or value.get("action") != kind):
            fail()
        if security:
            if "duplicate" in value and type(value["duplicate"]) is not bool:
                fail()
            uint(value.get("position"), nonzero=True)
        elif type(value.get("duplicate")) is not bool:
            fail()
        for key in ("batch", "lineage", "position"):
            if key in value:
                uint(value[key], nonzero=key != "lineage")
        if kind == "order":
            uint(value.get("position"), nonzero=True)
            if value.get("order_id") != order_id or type(value.get("held")) is not bool:
                fail()
    except AgentError:
        raise AgentError("outcome_unknown") from None
    result = {"status": "operator_journaled", "action": kind, "request_sha256": digest}
    for key in ("order_id", "position", "held", "duplicate", "batch", "lineage"):
        if key in value:
            result[key] = value[key]
    return result


def recovery_copy(value, request, digest):
    """One exact-original lookup reply bound to this request's digest."""
    try:
        _exact(value, ("schema", "request_sha256", "status", "action", "lineage",
                       "retry_authorized", *flag_keys(value)))
        status = value["status"]
        if (value["schema"] != RECOVERY_SCHEMA or value["request_sha256"] != digest
                or type(status) is not str or status not in RECOVERY
                or value["action"] != request["action"]["type"]
                or value["retry_authorized"] is not False):
            fail()
        if value["lineage"] is not None:
            uint(value["lineage"])
    except AgentError:
        raise AgentError("response_invalid") from None
    return {"schema": RECOVERY_SCHEMA, "status": RECOVERY[status], "wire_status": status,
            "request_sha256": digest, "action": value["action"], "lineage": value["lineage"],
            "retry_authorized": False}


def refusal_reason(value, status, digest):
    """The reason of an exact definitive ``not_placed`` refusal of ``digest``, else None."""
    if type(value) is not dict:
        return None
    try:
        _exact(value, ("error", "reason", "request_sha256", "retry", *flag_keys(value)))
    except AgentError:
        return None
    reason = value["reason"]
    if (value["error"] != "not_placed" or type(reason) is not str or reason not in NOT_PLACED
            or value["retry"] != NOT_PLACED[reason]
            or status != (429 if reason == "rate_limited" else 409)
            or value["request_sha256"] != digest):
        return None
    return reason


def _decimal(text, *, positive=False):
    if type(text) is not str or len(text) > 48 or _DECIMAL.fullmatch(text) is None:
        fail()
    if positive and re.search(r"[1-9]", text) is None:
        fail()
    whole, _, fraction = text.partition(".")
    return int(whole + fraction), len(fraction)


def units(value, unit, rounding=None):
    """Exact whole ``unit`` count of a decimal ``value``; off-unit input is refused.

    ``rounding`` "up"/"down" is used only for a MARKET order's protection bound.
    """
    try:
        a, a_scale = _decimal(value, positive=True)
        b, b_scale = _decimal(unit, positive=True)
    except AgentError:
        raise AgentError("invalid_order") from None
    numerator, denominator = a * 10 ** b_scale, b * 10 ** a_scale
    if rounding is None and numerator % denominator:
        fail("invalid_order")
    result = numerator // denominator
    if rounding == "up" and numerator % denominator:
        result += 1
    if not 0 < result <= U64:
        fail("invalid_order")
    return str(result)


def protection_ticks(opposite, tick, side):
    """A MARKET order's signed bound: 0.5% beyond the best opposite price, rounded
    to the tick away from the book (up for BUY, down for SELL)."""
    try:
        a, a_scale = _decimal(opposite, positive=True)
        b, b_scale = _decimal(tick, positive=True)
    except AgentError:
        raise AgentError("invalid_order") from None
    ratio = 1005 if side == "BUY" else 995
    numerator, denominator = a * ratio * 10 ** b_scale, 1000 * b * 10 ** a_scale
    result = numerator // denominator
    if side == "BUY" and numerator % denominator:
        result += 1
    if not 0 < result <= U64:
        fail("invalid_order")
    return str(result)


def market_rules(value, pins):
    """Tick, lot and best prices of one public TS2 market snapshot for these pins."""
    try:
        if (type(value) is not dict or value.get("schema") != MARKET_SCHEMA
                or value.get("environment") != "TESTNET" or value.get("symbol") != "BTCUSDC"
                or deployment({key: value.get(key) for key in _DOMAIN}) != deployment(pins)
                or any(value.get(key) is not False for key in _FLAGS[0])):
            fail()
        rules = value.get("market_rules")
        if type(rules) is not dict:
            fail()
        tick, step = rules.get("tick_size"), rules.get("step_size")
        _decimal(tick, positive=True)
        _decimal(step, positive=True)
        best = {}
        for side in ("bids", "asks"):
            levels = value.get(side)
            if type(levels) is not list or len(levels) > 64:
                fail()
            for level in levels:
                if type(level) is not dict:
                    fail()
                _decimal(level.get("price"), positive=True)
                _decimal(level.get("quantity"), positive=True)
            best[side] = levels[0]["price"] if levels else None
    except AgentError:
        raise AgentError("response_invalid") from None
    return {"tick_size": tick, "step_size": step,
            "best_bid": best["bids"], "best_ask": best["asks"]}
