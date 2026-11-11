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
#: TS2 season 2 (fill-only proving). Season 1 (0x3beb98f3…) keys are another deployment's.
SETTLEMENT = "0xfd5e17a1a722e0890669aa8c71a024ea4e4277bd"
CONFIG_HASH = "0x416ca6d7eeae85857f387b55e829052eb6474065874774f299e09c0484364bbd"
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
#: The bot-signed account read on ``/v1/ts2/agent/read`` and its reply.
READ_SCHEMA = "fairvenue-ts2-agent-read-v1"
VIEW_SCHEMA = "fairvenue-ts2-agent-view-v1"
READINESS_SCHEMA = "fairvenue-ts2-admission-readiness-v1"
#: The signed-read expiry must lie in (now, now + 60 s] (``read_auth.rs``).
READ_WINDOW_MS = 60_000
#: Default MARKET protection: 0.5% beyond the reference (the browser's bound).
DEFAULT_SLIPPAGE_BPS = 50
MAX_SLIPPAGE_BPS = 1_000

#: Delegation rights (fairvenue-protocol ``delegation.rs``).
TRADE, CANCEL, MODIFY, DEAD_MAN_SWITCH = 1, 2, 4, 8
#: Definitive admit refusal of an agent request: reason -> its only ``retry``.
#: HTTP 409, or 429 for ``rate_limited``. Nothing was journaled.
NOT_PLACED = {"invalid_request": "resign", "rejected": "resign",
              "admission_closed": "resign", "rate_limited": "resign"}
#: Exact-original lookup status on the wire -> the public outcome name.
#: ``unknown`` is never evidence that the request was not sent.
RECOVERY = {"operator_journaled": "accepted", "not_settled": "not_settled",
            "unknown": "unresolved"}
#: A ``not_placed`` lookup always carries one fixed ``reason`` (and no other
#: status does): ``rejected`` (journaled, then refused by the native writer)
#: or ``expired_never_admitted`` (never journaled and past its own expiry, so
#: nothing can admit it any more). Reason -> the public outcome name.
NOT_PLACED_RECOVERY = {"rejected": "rejected", "expired_never_admitted": "not_placed"}
#: Outcomes that retire exactly the looked-up original.
TERMINAL = ("accepted", "rejected", "not_placed", "not_settled")
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
    """One exact-original lookup reply bound to this request's digest.

    ``reason`` is exactly present with ``not_placed`` and absent otherwise.
    """
    try:
        status = value.get("status") if type(value) is dict else None
        reason = ("reason",) if status == "not_placed" else ()
        _exact(value, ("schema", "request_sha256", "status", "action", "lineage",
                       "retry_authorized", *reason, *flag_keys(value)))
        if (value["schema"] != RECOVERY_SCHEMA or value["request_sha256"] != digest
                or type(status) is not str
                or (status not in RECOVERY and status != "not_placed")
                or (reason and (type(value["reason"]) is not str
                                or value["reason"] not in NOT_PLACED_RECOVERY))
                or value["action"] != request["action"]["type"]
                or value["retry_authorized"] is not False):
            fail()
        if value["lineage"] is not None:
            uint(value["lineage"])
    except AgentError:
        raise AgentError("response_invalid") from None
    outcome = NOT_PLACED_RECOVERY[value["reason"]] if reason else RECOVERY[status]
    return {"schema": RECOVERY_SCHEMA, "status": outcome, "wire_status": status,
            "reason": value["reason"] if reason else None, "request_sha256": digest,
            "action": value["action"], "lineage": value["lineage"], "retry_authorized": False}


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


def slippage_bps(value):
    """A MARKET protection width in basis points: an int from 1 to 1,000 (10%)."""
    if type(value) is not int or not 1 <= value <= MAX_SLIPPAGE_BPS:
        fail("invalid_order")
    return value


def protection_ticks(opposite, tick, side, bps=DEFAULT_SLIPPAGE_BPS):
    """A MARKET order's signed bound: ``bps`` beyond the reference price (the best
    opposite price, or the mark when that side is empty), rounded to the tick
    away from the book (up for BUY, down for SELL). The default 50 bps is the
    browser's 0.5% bound (``protectedMarketPrice``)."""
    bps = slippage_bps(bps)
    try:
        a, a_scale = _decimal(opposite, positive=True)
        b, b_scale = _decimal(tick, positive=True)
    except AgentError:
        raise AgentError("invalid_order") from None
    ratio = 10_000 + bps if side == "BUY" else 10_000 - bps
    numerator, denominator = a * ratio * 10 ** b_scale, 10_000 * b * 10 ** a_scale
    result = numerator // denominator
    if side == "BUY" and numerator % denominator:
        result += 1
    if not 0 < result <= U64:
        fail("invalid_order")
    return str(result)


def times(count, unit):
    """The exact decimal string of ``count`` (a u64 string) whole ``unit`` steps."""
    c = int(uint(count))
    u, scale = _decimal(unit, positive=True)
    return _plain(c * u, scale)


def micros(value):
    """The exact decimal string of a micro-unit u64 string (6 decimals)."""
    return _plain(int(uint(value)), 6)


def _plain(number, scale):
    """``number / 10**scale`` as plain decimal text without trailing zeros."""
    sign = "-" if number < 0 else ""
    digits = str(abs(number)).rjust(scale + 1, "0")
    whole, fraction = (digits[:-scale], digits[-scale:]) if scale else (digits, "")
    fraction = fraction.rstrip("0")
    return sign + whole + ("." + fraction if fraction else "")


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
        mark = value.get("mark_price")
        if mark is not None:
            _decimal(mark, positive=True)
    except AgentError:
        raise AgentError("response_invalid") from None
    return {"tick_size": tick, "step_size": step,
            "best_bid": best["bids"], "best_ask": best["asks"], "mark_price": mark}


# Bot-signed account read ------------------------------------------------------

_SIGNED = re.compile(r"-?(0|[1-9][0-9]*)(\.[0-9]+)?")
_HASH = re.compile(r"0x[0-9a-f]{64}")
_VIEW = ("schema", *_DOMAIN, "source", "observed_at_ms", "market", "account", "orders", "fills",
         "history", "admission", "tradingEnabled", "readiness")
_READINESS = ("schema", "source", *_DOMAIN, "enabled", "reason", "observed_at_ms",
              "chain_block_number", "chain_block_hash", "chain_block_time_ms", "captured_at_ms",
              "expires_at_ms")
#: Single-provider TESTNET observation (2026-11-10), or an earlier writer's paired label.
READINESS_SOURCES = ("operator_single_provider_rpc_and_native_writer",
                     "operator_paired_rpc_and_native_writer")
_SOFT = ("wallet_balance", "reserved_order_margin", "locked_position_margin", "realized_pnl",
         "funding_paid", "position_quantity", "position_entry_price")
_SETTLED = ("batch_number", "lineage", "state_root", "transaction_hash", "block_hash",
            "block_number", "finalized_at_block", "wallet_balance", "realized_pnl",
            "funding_paid", "position_quantity", "exit_micros")
#: ``core.order_status`` words; a FairLag-held order is ``pending`` and has no detail.
ORDER_STATUSES = ("pending", "resting", "partially_filled", "filled", "canceled", "killed",
                  "expired", "rejected", "not_in_book", "unknown")
#: Orders that can still fill or rest: FairLag-held, resting or partly filled.
OPEN_ORDER_STATUSES = ("pending", "resting", "partially_filled")
_ORDER_DETAIL = ("side", "kind", "time_in_force", "limit_price_ticks", "quantity_lots",
                 "filled_lots", "expires_at_ms", "post_only", "reduce_only")
_FILL = ("position", "at_ms", "batch_number", "lineage", "kind", "role", "side", "order_id",
         "price_ticks", "quantity_lots", "fee", "settlement")
#: A fill's TS2 settlement state (``views.rs settlement``), never a V4 proof object.
SETTLEMENT_STATES = ("not_settled", "operator_soft", "operator_sealed",
                     "controller_observed_settled", "controller_observed_settled_final")
_CHAIN_SETTLEMENT = ("state", "state_root", "transaction_hash", "block_hash", "block_number",
                     "finalized_at_block")


def read_signing_message(pins, account_key, expires_at_ms):
    """``FV_TS2_READ_SIG\\0 || config || u8 1 || u64be account || 32 zero bytes || u64be
    expiry``: the native account read (kind 1, zero target). No nonce, no admission."""
    pins = deployment(pins)
    return (b"FV_TS2_READ_SIG\0" + fixed(pins["config_hash"], 32, nonzero=True) + bytes([1])
            + account_number(account_key).to_bytes(8, "big") + bytes(32)
            + _u64(uint(expires_at_ms, nonzero=True)))


def read_request(pins, account_key, agent_public_key, expires_at_ms, signature, crypto):
    """The exact ``fairvenue-ts2-agent-read-v1`` body, its signature verified."""
    pins = deployment(pins)
    fixed(agent_public_key, 32, nonzero=True)
    fixed(signature, 64)
    message = read_signing_message(pins, account_key, expires_at_ms)
    if not crypto.verify(signature, message, agent_public_key):
        fail("signature_invalid")
    return {"schema": READ_SCHEMA, **pins, "account_key": account_key,
            "agent_public_key": agent_public_key, "expires_at_ms": expires_at_ms,
            "signature": signature}


def _signed(text, *, signed=True):
    if (type(text) is not str or len(text) > 96
            or (_SIGNED if signed else _DECIMAL).fullmatch(text) is None):
        fail()
    return text


def _hash(text):
    if type(text) is not str or _HASH.fullmatch(text) is None or text == _ZERO:
        fail()
    return text


def _levels(rows):
    if type(rows) is not list or len(rows) > 20:
        fail()
    for row in rows:
        _exact(row, ("price", "quantity"))
        _decimal(row["price"], positive=True)
        _decimal(row["quantity"], positive=True)
    return [dict(row) for row in rows]


def _readiness(value, pins, observed, enabled):
    _exact(value, _READINESS)
    if (value["schema"] != READINESS_SCHEMA or value["source"] not in READINESS_SOURCES
            or deployment({key: value[key] for key in _DOMAIN}) != deployment(pins)
            or type(value["enabled"]) is not bool or value["enabled"] != enabled
            or type(value["reason"]) is not str or _REASON.fullmatch(value["reason"]) is None
            or value["enabled"] != (value["reason"] == "open")
            or value["observed_at_ms"] != observed):
        fail()
    chain = ("chain_block_number", "chain_block_hash", "chain_block_time_ms", "captured_at_ms",
             "expires_at_ms")
    present = [key for key in chain if value[key] is not None]
    if present and len(present) != len(chain):
        fail()
    if present:
        for key in chain:
            if key == "chain_block_hash":
                _hash(value[key])
            else:
                uint(value[key])
    elif enabled:
        fail()
    return {"reason": value["reason"], "source": value["source"],
            "expires_at_ms": value["expires_at_ms"]}


def _settled(value):
    if value is None:
        return None
    _exact(value, _SETTLED)
    for key in ("batch_number", "lineage", "block_number", "exit_micros"):
        uint(value[key])
    for key in ("state_root", "transaction_hash", "block_hash"):
        _hash(value[key])
    if value["finalized_at_block"] is not None:
        uint(value["finalized_at_block"])
    for key in ("wallet_balance", "realized_pnl", "funding_paid", "position_quantity"):
        _signed(value[key])
    return dict(value)


def _fill_settlement(value):
    state = value.get("state") if type(value) is dict else None
    if state in ("controller_observed_settled", "controller_observed_settled_final"):
        _exact(value, _CHAIN_SETTLEMENT)
        _hash(value["state_root"])
        _hash(value["transaction_hash"])
        _hash(value["block_hash"])
        uint(value["block_number"])
        if (value["finalized_at_block"] is None) != (state == "controller_observed_settled"):
            fail()
        if value["finalized_at_block"] is not None:
            uint(value["finalized_at_block"])
    elif state in SETTLEMENT_STATES:
        _exact(value, ("state",))
    else:
        fail()
    return dict(value)


def view_copy(value, pins):
    """One strict ``fairvenue-ts2-agent-view-v1`` reply of the signed account read.

    Prices and sizes are exact decimal strings: order and fill ticks and lots
    are converted with the reply's own tick and step sizes. A FairLag-held
    order (``pending``) never shows its side, price or size. Soft, operator-
    trusted state: never a proof, L1 finality or a release claim.
    """
    try:
        _exact(value, (*_VIEW, *flag_keys(value)))
        if (value["schema"] != VIEW_SCHEMA
                or deployment({key: value[key] for key in _DOMAIN}) != deployment(pins)
                or value["source"] != "operator_local_native_and_controller_observed_chain"
                or type(value["tradingEnabled"]) is not bool
                or type(value["admission"]) is not str
                or _REASON.fullmatch(value["admission"]) is None
                or (value["tradingEnabled"] and value["admission"] != "open")):
            fail()
        observed = uint(value["observed_at_ms"], nonzero=True)
        readiness = _readiness(value["readiness"], pins, observed, value["tradingEnabled"])
        market = value["market"]
        _exact(market, ("symbol", "tick_size", "step_size", "fairlag_ms", "book", "mark"))
        tick, step = market["tick_size"], market["step_size"]
        _decimal(tick, positive=True)
        _decimal(step, positive=True)
        uint(market["fairlag_ms"])
        book = market["book"]
        _exact(book, ("source", "snapshot", "depth", "bids", "asks"))
        if (market["symbol"] != "BTCUSDC" or book["source"] != "native_matcher"
                or book["snapshot"] is not True or type(book["depth"]) is not int
                or book["depth"] != 20):
            fail()
        bids, asks = _levels(book["bids"]), _levels(book["asks"])
        mark = market["mark"]
        _exact(mark, ("price_micros", "observed_at_ms"))
        if (mark["price_micros"] is None) != (mark["observed_at_ms"] is None):
            fail()
        mark_price = None
        if mark["price_micros"] is not None:
            mark_price = micros(uint(mark["price_micros"], nonzero=True))
            uint(mark["observed_at_ms"])
        account = value["account"]
        _exact(account, ("soft", "settled", "settled_final"))
        soft = account["soft"]
        if soft is not None:
            _exact(soft, _SOFT)
            for key in ("wallet_balance", "realized_pnl", "funding_paid", "position_quantity"):
                _signed(soft[key])
            for key in ("reserved_order_margin", "locked_position_margin"):
                _signed(soft[key], signed=False)
            if soft["position_entry_price"] is not None:
                _signed(soft["position_entry_price"], signed=False)
            soft = dict(soft)
        settled, settled_final = _settled(account["settled"]), _settled(account["settled_final"])
        if (settled_final is not None and settled_final["finalized_at_block"] is None) or (
                soft is None and (settled is not None or settled_final is not None)):
            fail()
        orders = value["orders"]
        if type(orders) is not list or len(orders) > 64:
            fail()
        order_rows = []
        for row in orders:
            status = row.get("status") if type(row) is dict else None
            if status not in ORDER_STATUSES:
                fail()
            pending = status == "pending"
            _exact(row, ("order_id", "status", "lineage", "admitted_at_ms",
                         *(() if pending else ("detail",))))
            fixed(row["order_id"], 32, nonzero=True)
            uint(row["lineage"])
            uint(row["admitted_at_ms"])
            order = {"order_id": row["order_id"], "status": status, "lineage": row["lineage"],
                     "admitted_at_ms": row["admitted_at_ms"]}
            if not pending:
                detail = row["detail"]
                _exact(detail, _ORDER_DETAIL)
                if (detail["side"] not in ("buy", "sell")
                        or detail["kind"] not in ("limit", "market")
                        or detail["time_in_force"] not in ("GTC", "IOC", "FOK")
                        or type(detail["post_only"]) is not bool
                        or type(detail["reduce_only"]) is not bool):
                    fail()
                lots, filled = int(uint(detail["quantity_lots"])), int(uint(detail["filled_lots"]))
                if filled > lots:
                    fail()
                order.update(
                    side=detail["side"].upper(), order_type=detail["kind"].upper(),
                    time_in_force=detail["time_in_force"],
                    price=times(detail["limit_price_ticks"], tick),
                    quantity=times(detail["quantity_lots"], step),
                    filled=times(detail["filled_lots"], step),
                    remaining=times(str(lots - filled), step),
                    expires_at_ms=uint(detail["expires_at_ms"]),
                    post_only=detail["post_only"], reduce_only=detail["reduce_only"])
            order_rows.append(order)
        fills = value["fills"]
        if type(fills) is not list or len(fills) > 64:
            fail()
        fill_rows = []
        for row in fills:
            _exact(row, _FILL)
            if (row["kind"] not in ("fill", "liquidation", "close")
                    or row["role"] not in ("taker", "maker") or row["side"] not in ("buy", "sell")):
                fail()
            for key in ("position", "at_ms", "batch_number", "lineage"):
                uint(row[key])
            fixed(row["order_id"], 32, nonzero=True)
            fill_rows.append({
                "position": row["position"], "at_ms": row["at_ms"], "kind": row["kind"],
                "role": row["role"], "side": row["side"].upper(), "order_id": row["order_id"],
                "price": times(uint(row["price_ticks"], nonzero=True), tick),
                "quantity": times(uint(row["quantity_lots"], nonzero=True), step),
                "fee": _signed(row["fee"]), "batch_number": row["batch_number"],
                "lineage": row["lineage"], "settlement": _fill_settlement(row["settlement"])})
        history = value["history"]
        _exact(history, ("order_count", "fill_count", "row_limit", "orders_truncated",
                         "fills_truncated"))
        uint(history["order_count"])
        uint(history["fill_count"])
        if (type(history["row_limit"]) is not int or history["row_limit"] != 64
                or type(history["orders_truncated"]) is not bool
                or type(history["fills_truncated"]) is not bool):
            fail()
    except AgentError:
        raise AgentError("response_invalid") from None
    return {"schema": VIEW_SCHEMA, "observed_at_ms": observed,
            "trading_enabled": value["tradingEnabled"], "admission": value["admission"],
            "readiness": readiness,
            "market": {"symbol": "BTCUSDC", "tick_size": tick, "step_size": step,
                       "fairlag_ms": market["fairlag_ms"], "bids": bids, "asks": asks,
                       "mark_price": mark_price, "mark_observed_at_ms": mark["observed_at_ms"]},
            "account": soft, "settled": settled, "settled_final": settled_final,
            "orders": order_rows, "fills": fill_rows, "history": dict(history)}
