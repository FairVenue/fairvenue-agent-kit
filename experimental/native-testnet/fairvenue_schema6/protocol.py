"""Bounded schema6 public DTOs and exact native signing bytes; no authority issuer."""

from __future__ import annotations

import copy
import json
import re

SAFE = (1 << 53) - 1  # Existing Rust JCS uses binary64, even in Python.
U64 = (1 << 64) - 1
SCHEMA = "fairvenue-fva8-account-free-agent-v1"
GRANT_SCHEMA = "fairvenue-v1-agent-public-grant-v1"


# Definitive operator-beta admit refusal (`"error":"not_placed"`): reason ->
# the only `retry` the server pairs with it. HTTP 409, except 429 for
# `rate_limited`. The exact signed request was provably not placed (nothing
# durable, nonce not consumed, not bound), so its retained original clears.
NOT_PLACED = {
    "batch_closed": "resign", "stale_context": "resign", "nonce_stale": "resign",
    "intent_expired": "resign", "bad_signature": "resign", "unknown_agent": "resign",
    "invalid_request": "resign", "rate_limited": "same_or_resign", "admission_closed": "later",
}


class AgentError(Exception):
    """Content-free failures; never include a key, request or upstream error."""

    def __init__(self, code="invalid_input"):
        self.code = code if code in (
            "invalid_input", "crypto_unavailable", "context_changed", "signature_invalid",
            "activation_closed", "busy", "deadline", "unavailable", "response_invalid",
            "outcome_unknown", "no_pending_request", "nonce_reused", *NOT_PLACED,
        ) else "unavailable"
        super().__init__("Agent request refused; retain any pending signed request before retrying.")


class AdmitRefusal(AgentError):
    """A public non-2xx admit reply (409/429), not yet validated or trusted."""

    def __init__(self, status, body):
        super().__init__("outcome_unknown")
        self.status, self.body = status, body


def fail(code="invalid_input"):
    raise AgentError(code)


def exact(value, keys):
    if type(value) is not dict or set(value) != set(keys):
        fail()


def decimal(value, *, zero=False, safe=False):
    if type(value) is not str or len(value) > 20 or not re.fullmatch(r"0|[1-9][0-9]*", value):
        fail()
    number = int(value)
    if number > (SAFE if safe else U64) or (not zero and number == 0):
        fail()
    return number


def integer(value, maximum=SAFE):
    if type(value) is not int or not 0 <= value <= maximum:
        fail()
    return value


def hex_value(value, size=32, *, nonzero=True):
    if type(value) is not str or not re.fullmatch("0x[0-9a-f]{" + str(size * 2) + "}", value):
        fail()
    if nonzero and int(value, 16) == 0:
        fail()
    return value


def byte_array(value, size):
    if type(value) is not list or len(value) != size or not any(value):
        fail()
    return [integer(item, 255) for item in value]


def encoded_hex(value):
    return "0x" + bytes(value).hex()


def uint(value, width=8):
    return value.to_bytes(width, "big")


def jcs(value):
    # Only validated ASCII enums/keys, integers <= SAFE, booleans and decimal strings.
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def cbor(value, depth=0):
    """Known native serde field order, not a generic canonical-map serializer."""
    if depth > 8:
        fail()

    def head(major, number):
        if number < 24:
            return bytes([major << 5 | number])
        for width, tag in ((1, 24), (2, 25), (4, 26), (8, 27)):
            if number < 1 << (width * 8):
                return bytes([major << 5 | tag]) + uint(number, width)
        fail()

    if value is None:
        return b"\xf6"
    if type(value) is bool:
        return b"\xf5" if value else b"\xf4"
    if type(value) is int:
        return head(0, integer(value))
    if type(value) is str:
        data = value.encode("utf-8")
        if len(data) > 256:
            fail()
        return head(3, len(data)) + data
    if type(value) is list and len(value) <= 32:
        return head(4, len(value)) + b"".join(cbor(item, depth + 1) for item in value)
    if type(value) is dict and len(value) <= 12:
        return head(5, len(value)) + b"".join(
            cbor(key, depth + 1) + cbor(item, depth + 1) for key, item in value.items()
        )
    fail()


def prefix_copy(value, *, retry=False):
    exact(value, ("domain", "opening", "head"))
    exact(value["domain"], ("chain_id", "settlement"))
    if type(value["domain"]["chain_id"]) is not int or value["domain"]["chain_id"] != 421614:
        fail()
    domain = {"chain_id": 421614, "settlement": byte_array(value["domain"]["settlement"], 20)}
    exact(value["opening"], ("previous_root", "batch", "generation", "opening_commitment"))
    opening = {
        "previous_root": byte_array(value["opening"]["previous_root"], 32),
        "batch": integer(value["opening"]["batch"]),
        "generation": integer(value["opening"]["generation"]),
        "opening_commitment": byte_array(value["opening"]["opening_commitment"], 32),
    }
    if not opening["batch"] or not opening["generation"]:
        fail()
    exact(value["head"], ("count", "chain_hash", "public_data_hash", "next_root"))
    count = integer(value["head"]["count"], 512 if retry else 511)
    head = {
        "count": count,
        "chain_hash": hex_value(value["head"]["chain_hash"], nonzero=count != 0),
        "public_data_hash": hex_value(value["head"]["public_data_hash"], nonzero=count != 0),
        "next_root": hex_value(value["head"]["next_root"]),
    }
    if count == 0 and (
        head["chain_hash"] != "0x" + "0" * 64
        or head["public_data_hash"] != "0x" + "0" * 64
        or head["next_root"] != encoded_hex(opening["previous_root"])
    ):
        fail()
    return {"domain": domain, "opening": opening, "head": head}


def policy_copy(value, crypto):
    scalars = (
        "config_version", "epoch_ms", "absolute_error_ms", "max_disagreement_epochs",
        "max_lifetime_epochs", "max_oracle_age_ms", "max_oracle_future_ms",
    )
    exact(value, ("domain", "market_id", *scalars, "gateways"))
    exact(value["domain"], ("chain_id", "settlement"))
    if type(value["domain"]["chain_id"]) is not int or value["domain"]["chain_id"] != 421614:
        fail()
    domain = {"chain_id": 421614, "settlement": byte_array(value["domain"]["settlement"], 20)}
    market = byte_array(value["market_id"], 32)
    numbers = [decimal(value[key], zero=True) for key in scalars]
    version, epoch, error, disagreement, lifetime, age, future = numbers
    if (version not in (1, 2, 3) or not 1 <= epoch <= 100 or error > epoch
            or disagreement > 10 or lifetime == 0 or not 1 <= age <= 30000 or future > 1000):
        fail()
    if type(value["gateways"]) is not list or not 3 <= len(value["gateways"]) <= 6:
        fail()
    gateways = []
    for key in value["gateways"]:
        exact(key, ("gateway_id", "key_id", "public_key", "first_epoch", "end_epoch"))
        entry = {
            "gateway_id": integer(key["gateway_id"], 65535),
            "key_id": integer(key["key_id"], 0xffffffff),
            "public_key": hex_value(key["public_key"]),
            "first_epoch": str(decimal(key["first_epoch"], zero=True)),
            "end_epoch": str(decimal(key["end_epoch"])),
        }
        if not crypto.strong(entry["public_key"]) or int(entry["first_epoch"]) >= int(entry["end_epoch"]):
            fail()
        gateways.append(entry)
    if len({key["gateway_id"] for key in gateways}) != 3:
        fail()
    for index, key in enumerate(gateways):
        if sum(other["gateway_id"] == key["gateway_id"] for other in gateways) > 2:
            fail()
        for other in gateways[index + 1:]:
            if key["public_key"] == other["public_key"] or (
                key["gateway_id"] == other["gateway_id"] and (
                    key["key_id"] == other["key_id"] or (
                        int(key["first_epoch"]) < int(other["end_epoch"])
                        and int(other["first_epoch"]) < int(key["end_epoch"])
                    )
                )
            ):
                fail()
    gateways.sort(key=lambda key: (key["gateway_id"], key["key_id"], int(key["first_epoch"])))
    data = (b"FairVenue.v2.time-policy.v1\0" + uint(421614) + bytes(domain["settlement"])
            + bytes(market) + b"".join(uint(number) for number in (*numbers, 4096, 100))
            + uint(len(gateways), 2))
    for key in gateways:
        data += (uint(key["gateway_id"], 2) + uint(key["key_id"], 4)
                 + bytes.fromhex(key["public_key"][2:]) + uint(int(key["first_epoch"]))
                 + uint(int(key["end_epoch"])))
    return {"domain": domain, "market_id": market, **{key: value[key] for key in scalars},
            "gateways": gateways, "commitment": crypto.digest(data)}


def grant_copy(value, policy, crypto):
    exact(value, ("schema", "chainId", "settlement", "owner", "account", "agentPublicKey",
                  "permissions", "delegationNonce", "issuedAtMs", "expiresAtMs", "maxExpiryMs",
                  "canWithdraw"))
    if (value["schema"] != GRANT_SCHEMA or value["chainId"] != "421614"
            or hex_value(value["settlement"], 20) != encoded_hex(policy["domain"]["settlement"])
            or value["canWithdraw"] is not False or not integer(value["permissions"], 15)):
        fail()
    hex_value(value["owner"], 20)
    account = hex_value(value["account"])
    if not re.fullmatch(r"0x0{48}[0-9a-f]{16}", account) or int(account, 16) > SAFE:
        fail()
    if not crypto.strong(hex_value(value["agentPublicKey"])):
        fail()
    issued = decimal(value["issuedAtMs"], zero=True, safe=True)
    expiry = decimal(value["expiresAtMs"], safe=True)
    maximum = decimal(value["maxExpiryMs"], safe=True)
    decimal(value["delegationNonce"], safe=True)
    if not issued < expiry <= maximum <= issued + 30 * 86400000:
        fail()
    return copy.deepcopy(value)


def order_copy(value):
    exact(value, ("symbol", "side", "order_type", "time_in_force", "price", "quantity",
                  "reduce_only", "post_only"))

    def money(text):
        if (type(text) is not str or len(text) > 48
                or not re.fullmatch(r"(0|[1-9][0-9]*)(\.[0-9]+)?", text)
                or not re.search(r"[1-9]", text)):
            fail()
        whole, _, fraction = text.partition(".")
        if len(fraction) > 28 or int(whole + fraction) >= 1 << 96:
            fail()
        return text

    if (type(value["symbol"]) is not str or not re.fullmatch(r"[A-Z0-9]{1,24}", value["symbol"])
            or value["side"] not in ("BUY", "SELL") or value["order_type"] not in ("LIMIT", "MARKET")
            or value["time_in_force"] not in ("GTC", "IOC", "FOK")
            or type(value["reduce_only"]) is not bool or type(value["post_only"]) is not bool):
        fail()
    return {
        "symbol": value["symbol"], "side": value["side"], "order_type": value["order_type"],
        "time_in_force": value["time_in_force"],
        "price": None if value["price"] is None else money(value["price"]),
        "quantity": money(value["quantity"]), "reduce_only": value["reduce_only"],
        "post_only": value["post_only"],
    }


def action_copy(value):
    if type(value) is not dict:
        fail()
    kind = value.get("type")
    if kind == "place":
        exact(value, ("type", "order"))
        return {"type": kind, "order": order_copy(value["order"])}, 1
    if kind == "modify":
        exact(value, ("type", "order_id", "replacement"))
        decimal(value["order_id"], safe=True)
        return {"type": kind, "order_id": value["order_id"],
                "replacement": order_copy(value["replacement"])}, 4
    if kind == "cancel":
        exact(value, ("type", "order_id"))
        decimal(value["order_id"], safe=True)
        return {"type": kind, "order_id": value["order_id"]}, 2
    if kind == "fee_mode":
        exact(value, ("type", "mode"))
        if value["mode"] not in ("FIXED", "PNL"):
            fail()
        return {"type": kind, "mode": value["mode"]}, 1
    if kind == "leverage":
        exact(value, ("type", "leverage"))
        if decimal(value["leverage"]) > 0xffffffff:
            fail()
        return {"type": kind, "leverage": value["leverage"]}, 1
    if kind == "dead_man_switch":
        exact(value, ("type", "deadline_ms"))
        decimal(value["deadline_ms"], zero=True, safe=True)
        return {"type": kind, "deadline_ms": value["deadline_ms"]}, 8
    fail()


def context_copy(value, policy, grant):
    exact(value, ("authenticated", "session_scope", "owner", "prefix", "protocol_time_ms"))
    scope = value["session_scope"]
    if (value["authenticated"] is not True or type(scope) is not str
            or not re.fullmatch(r"[0-9a-f]{64}", scope) or not int(scope, 16)
            or value["owner"] != grant["owner"]):
        fail("context_changed")
    prefix = prefix_copy(value["prefix"], retry=True)
    now = decimal(value["protocol_time_ms"], zero=True, safe=True)
    if (prefix["domain"] != policy["domain"]
            or not int(grant["issuedAtMs"]) <= now < int(grant["expiresAtMs"])):
        fail("context_changed")
    return {"authenticated": True, "session_scope": scope, "owner": value["owner"],
            "prefix": prefix, "protocol_time_ms": value["protocol_time_ms"]}


def build(policy, grant, context, value, crypto):
    exact(value, ("nonce", "expiresAtMs", "action"))
    nonce = decimal(value["nonce"], safe=True)
    expiry = decimal(value["expiresAtMs"], safe=True)
    if (context["prefix"]["head"]["count"] >= 512
            or not int(context["protocol_time_ms"]) < expiry <= int(grant["expiresAtMs"])):
        fail("context_changed")
    action, permission = action_copy(value["action"])
    if not grant["permissions"] & permission:
        fail()
    order = action.get("order", action.get("replacement"))
    if order and crypto.digest(b"FairVenue.v2.market-id.v1\0" + order["symbol"].encode()) != encoded_hex(policy["market_id"]):
        fail()
    intent = {"schema": SCHEMA, "expected": prefix_copy(context["prefix"]),
              "agent_public_key": grant["agentPublicKey"], "nonce": value["nonce"],
              "expires_at_ms": value["expiresAtMs"], "action": action}
    account = int(grant["account"], 16)
    variants = {"place": "Place", "cancel": "Cancel", "modify": "Modify", "fee_mode": "FeeMode",
                "leverage": "Leverage", "dead_man_switch": "DeadManSwitch"}
    fields = {key: item for key, item in action.items() if key != "type"}
    for field in ("order_id", "leverage", "deadline_ms"):
        if field in fields:
            fields[field] = int(fields[field])
    command = {"account": account, "agent": list(bytes.fromhex(grant["agentPublicKey"][2:])),
               "nonce": nonce, "expires_at_ms": expiry,
               "action": {variants[action["type"]]: {"account": account, **fields}}}
    ordinary = crypto.digest(b"FairVenue.isolated.agent-command.v2\0" + uint(421614)
                             + bytes(policy["domain"]["settlement"])
                             + jcs({"command": command, "delegation_nonce": int(grant["delegationNonce"])}).encode())
    opening = intent["expected"]["opening"]
    generation = (b"FV_V4_AGENT_COMMAND_OPENING\0" + uint(6, 2) + uint(421614)
                  + bytes(policy["domain"]["settlement"]) + bytes(policy["market_id"])
                  + uint(int(policy["config_version"])) + bytes.fromhex(policy["commitment"][2:])
                  + bytes(opening["previous_root"]) + uint(opening["batch"]) + uint(opening["generation"])
                  + bytes(opening["opening_commitment"]) + uint(intent["expected"]["head"]["count"], 4)
                  + bytes.fromhex(ordinary[2:]))
    preliminary = b"FairVenue.FVA8.account-free-agent-intent.v1\0" + cbor(intent)
    if len(preliminary) > 8192:
        fail()
    return {"intent": intent, "intent_signing_bytes": encoded_hex(preliminary),
            "ordinary_signing_hash": ordinary, "generation_signing_bytes": encoded_hex(generation)}


def build_agent_command_signables(*, policy, grant, context, input, crypto=None):
    """Pure reconstruction. Supplied pins/grant are not promoted to authority."""
    from .crypto import CryptoPrimitives

    if crypto is None:
        crypto = CryptoPrimitives()
    if type(crypto) is not CryptoPrimitives:
        fail("crypto_unavailable")
    pinned = policy_copy(policy, crypto)
    public_grant = grant_copy(grant, pinned, crypto)
    return build(pinned, public_grant, context_copy(context, pinned, public_grant), input, crypto)
