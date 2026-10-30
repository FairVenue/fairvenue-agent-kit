"""Validated native market snapshots and exact decimal helpers.

The public book is native resting liquidity on the operator-run TESTNET, never
an external venue's book. Snapshots are soft (operator-trusted) state, not
proof-verified or L1-final.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal
from typing import Any

SYMBOL = "BTCUSDC"
EXPLORER_TX = "https://sepolia.arbiscan.io/tx/"

_DECIMAL = re.compile(r"(0|[1-9][0-9]*)(\.[0-9]+)?")
_UINT = re.compile(r"0|[1-9][0-9]{0,19}")
_HASH = re.compile(r"0x[0-9a-f]{64}")


class MarketDataError(Exception):
    """The public reply did not match the pinned native TESTNET market."""


def _fail(what: str) -> MarketDataError:
    return MarketDataError(f"Unexpected public market data ({what}). Nothing was assumed.")


def decimal_text(value: object, *, positive: bool = False, what: str = "decimal") -> Decimal:
    if not isinstance(value, str) or len(value) > 80 or _DECIMAL.fullmatch(value) is None:
        raise _fail(what)
    number = Decimal(value)
    if positive and number <= 0:
        raise _fail(what)
    return number


def uint(value: object, *, what: str = "integer") -> int:
    if isinstance(value, bool):
        raise _fail(what)
    if isinstance(value, int):
        if value < 0:
            raise _fail(what)
        return value
    if not isinstance(value, str) or _UINT.fullmatch(value) is None:
        raise _fail(what)
    return int(value)


def fmt(value: Decimal) -> str:
    """Plain decimal text without exponent or trailing zeros, as the wire expects."""
    text = format(value.normalize(), "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def round_to_step(value: Decimal, step: Decimal, *, up: bool) -> Decimal:
    units = (value / step).to_integral_value(rounding=ROUND_CEILING if up else ROUND_FLOOR)
    return units * step


def utc(milliseconds: int) -> str:
    return (
        datetime.fromtimestamp(milliseconds / 1000, tz=UTC)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


@dataclass(frozen=True)
class Level:
    price: Decimal
    quantity: Decimal

    def view(self) -> dict[str, str]:
        return {"price": fmt(self.price), "quantity": fmt(self.quantity)}


@dataclass(frozen=True)
class Trade:
    trade_id: str
    price: Decimal
    quantity: Decimal
    taker_side: str
    time_ms: int
    proof: Mapping[str, Any]

    def view(self) -> dict[str, Any]:
        status = self.proof.get("status")
        proof: dict[str, Any] = {
            "status": status
            if status in ("pending", "sp1_verified_operator_committed")
            else "unknown"
        }
        batch = self.proof.get("batch_number")
        if isinstance(batch, str) and _UINT.fullmatch(batch):
            proof["batch"] = batch
        for key, label in (
            ("proof_registration_transaction_hash", "registration_tx"),
            ("finalization_transaction_hash", "finalization_tx"),
        ):
            value = self.proof.get(key)
            if isinstance(value, str) and _HASH.fullmatch(value):
                proof[label] = EXPLORER_TX + value
        return {
            "trade_id": self.trade_id,
            "price": fmt(self.price),
            "quantity": fmt(self.quantity),
            "taker_side": self.taker_side,
            "time": utc(self.time_ms),
            "proof": proof,
        }


@dataclass(frozen=True)
class Market:
    protocol_time_ms: int
    mark_price: Decimal | None
    batch: int
    step: int | None
    state_root: str
    bids: tuple[Level, ...]
    asks: tuple[Level, ...]
    trades: tuple[Trade, ...]
    fees: dict[str, str]
    tick_size: Decimal
    step_size: Decimal
    fairlag_ms: int
    voided_batches: tuple[Mapping[str, Any], ...]
    native_policy: Any

    @property
    def best_bid(self) -> Level | None:
        return self.bids[0] if self.bids else None

    @property
    def best_ask(self) -> Level | None:
        return self.asks[0] if self.asks else None

    @property
    def mid_price(self) -> Decimal | None:
        if self.best_bid is None or self.best_ask is None:
            return None
        return (self.best_bid.price + self.best_ask.price) / 2

    @property
    def reference_price(self) -> Decimal | None:
        """The midpoint when both sides rest, otherwise the native mark."""
        return self.mid_price if self.mid_price is not None else self.mark_price

    def fingerprint(self) -> tuple[Any, ...]:
        return (
            self.mark_price,
            self.batch,
            self.step,
            self.bids[:5],
            self.asks[:5],
            self.trades[0].trade_id if self.trades else None,
        )


def _levels(rows: object, *, descending: bool) -> tuple[Level, ...]:
    if not isinstance(rows, list) or len(rows) > 50:
        raise _fail("book levels")
    levels: list[Level] = []
    for row in rows:
        if not isinstance(row, dict):
            raise _fail("book level")
        level = Level(
            decimal_text(row.get("price"), positive=True, what="level price"),
            decimal_text(row.get("quantity"), positive=True, what="level quantity"),
        )
        if levels and (
            (descending and levels[-1].price <= level.price)
            or (not descending and levels[-1].price >= level.price)
        ):
            raise _fail("book order")
        levels.append(level)
    return tuple(levels)


def parse_market(value: Mapping[str, Any], settlement: str) -> Market:
    """Validate one ``native_market`` reply against the installed deployment pin."""
    if (
        value.get("schema") != "fairvenue-operator-beta-market-v1"
        or value.get("environment") != "TESTNET"
        or value.get("chain_id") != 421614
        or value.get("settlement") != settlement
        or value.get("symbol") != SYMBOL
        or value.get("source") != "operator_trusted_testnet_soft_snapshot"
        or value.get("proof_backed") is not False
        or value.get("l1_finality_verified") is not False
        or value.get("public_release_go") is not False
    ):
        raise _fail("deployment or market identity")
    prefix = value.get("prefix")
    if not isinstance(prefix, dict):
        raise _fail("prefix")
    opening, head = prefix.get("opening"), prefix.get("head")
    if not isinstance(opening, dict) or not isinstance(head, dict):
        raise _fail("prefix")
    root = head.get("next_root")
    if not isinstance(root, str) or _HASH.fullmatch(root) is None:
        raise _fail("state root")
    step = head.get("count")
    bids = _levels(value.get("bids"), descending=True)
    asks = _levels(value.get("asks"), descending=False)
    if bids and asks and bids[0].price >= asks[0].price:
        raise _fail("crossed book")
    trades: list[Trade] = []
    for row in value.get("trades") or []:
        if not isinstance(row, dict) or row.get("side") not in ("BUY", "SELL"):
            raise _fail("trade")
        proof = row.get("proof")
        trades.append(
            Trade(
                trade_id=str(uint(row.get("trade_id"), what="trade id")),
                price=decimal_text(row.get("price"), positive=True, what="trade price"),
                quantity=decimal_text(row.get("quantity"), positive=True, what="trade quantity"),
                taker_side=row["side"],
                time_ms=uint(row.get("timestamp_ms"), what="trade time"),
                proof=proof if isinstance(proof, dict) else {},
            )
        )
    fees = value.get("fee_schedule")
    rules = value.get("market_rules")
    if not isinstance(fees, dict) or not isinstance(rules, dict):
        raise _fail("fees or rules")
    fee_view = {
        key: fmt(decimal_text(fees.get(key), what="fee"))
        for key in (
            "fixed_maker_rate",
            "fixed_taker_rate",
            "pnl_base_rate",
            "pnl_taker_surcharge_rate",
        )
    }
    mark = value.get("mark_price")
    voided = value.get("voided_batches") or []
    return Market(
        protocol_time_ms=uint(value.get("protocol_time_ms"), what="protocol time"),
        mark_price=None if mark is None else decimal_text(mark, positive=True, what="mark"),
        batch=uint(opening.get("batch"), what="batch"),
        step=step if isinstance(step, int) and not isinstance(step, bool) else None,
        state_root=root,
        bids=bids,
        asks=asks,
        trades=tuple(trades),
        fees=fee_view,
        tick_size=decimal_text(rules.get("tick_size"), positive=True, what="tick size"),
        step_size=decimal_text(rules.get("step_size"), positive=True, what="step size"),
        fairlag_ms=uint(rules.get("fairlag_ms"), what="FairLag"),
        voided_batches=tuple(
            view for item in (voided if isinstance(voided, list) else []) if (view := _voided(item))
        ),
        native_policy=value.get("native_policy"),
    )


def _voided(item: object) -> dict[str, Any] | None:
    """Only the known public fields of a voided batch, with checked types."""
    if not isinstance(item, dict):
        return None
    view: dict[str, Any] = {}
    for key in ("batch", "generation", "replacement_generation"):
        if isinstance(item.get(key), int) and not isinstance(item.get(key), bool):
            view[key] = item[key]
    transaction = item.get("replacement_transaction")
    if isinstance(transaction, str) and _HASH.fullmatch(transaction):
        view["replacement_transaction"] = transaction
    reason = item.get("reason")
    if isinstance(reason, str) and len(reason) <= 32 and reason.replace("_", "").isalpha():
        view["reason"] = reason
    return view if "batch" in view else None


def market_view(market: Market, *, depth: int, trades: int) -> dict[str, Any]:
    """A decision-ready summary for an agent. Prices are exact decimal strings."""
    best_bid, best_ask = market.best_bid, market.best_ask
    spread = spread_bps = None
    if best_bid is not None and best_ask is not None and market.mid_price:
        spread = best_ask.price - best_bid.price
        spread_bps = fmt((spread / market.mid_price * 10_000).quantize(Decimal("0.01")))
    return {
        "symbol": SYMBOL,
        "environment": "TESTNET (Arbitrum Sepolia mock tokens, no real funds)",
        "as_of": utc(market.protocol_time_ms),
        "protocol_time_ms": str(market.protocol_time_ms),
        "batch": str(market.batch),
        "step": market.step,
        "mark_price": None if market.mark_price is None else fmt(market.mark_price),
        "best_bid": None if best_bid is None else best_bid.view(),
        "best_ask": None if best_ask is None else best_ask.view(),
        "mid_price": None if market.mid_price is None else fmt(market.mid_price),
        "spread": None if spread is None else fmt(spread),
        "spread_bps": spread_bps,
        "bids": [level.view() for level in market.bids[:depth]],
        "asks": [level.view() for level in market.asks[:depth]],
        "recent_trades": [trade.view() for trade in market.trades[:trades]],
        "fees": market.fees,
        "rules": {
            "tick_size": fmt(market.tick_size),
            "step_size": fmt(market.step_size),
            "fairlag_ms": str(market.fairlag_ms),
            "order_types": "LIMIT only: GTC (optionally post-only) or price-protected IOC",
        },
        "voided_batches": [dict(item) for item in market.voided_batches[:3]],
        "finality": "soft operator-trusted snapshot, not proof-verified or L1-final",
    }


def parse_history(
    value: Mapping[str, Any], kind: str, settlement: str
) -> list[tuple[int, Decimal]]:
    schema, source = {
        "book": ("fairvenue-native-book-history-v1", "native_order_book_midpoint_snapshots"),
        "mark": ("fairvenue-native-mark-history-v1", "native_admitted_oracle_observations"),
    }[kind]
    marks = value.get("marks")
    if (
        value.get("schema") != schema
        or value.get("source") != source
        or value.get("environment") != "TESTNET"
        or value.get("chain_id") != 421614
        or value.get("settlement") != settlement
        or value.get("symbol") != SYMBOL
        or value.get("proof_backed") is not False
        or not isinstance(marks, list)
        or len(marks) > 4096
    ):
        raise _fail("price history")
    points: list[tuple[int, Decimal]] = []
    for row in marks:
        if not isinstance(row, dict):
            raise _fail("history point")
        time_ms = uint(row.get("time_ms"), what="history time")
        if points and time_ms <= points[-1][0]:
            raise _fail("history order")
        points.append(
            (time_ms, decimal_text(row.get("price"), positive=True, what="history price"))
        )
    return points


def history_view(points: Sequence[tuple[int, Decimal]], *, limit: int) -> dict[str, Any]:
    if not points:
        return {"points": [], "count": 0}
    recent = list(points[-limit:])
    prices = [price for _, price in recent]
    first, last = prices[0], prices[-1]
    return {
        "count": len(recent),
        "from": utc(recent[0][0]),
        "to": utc(recent[-1][0]),
        "first": fmt(first),
        "last": fmt(last),
        "low": fmt(min(prices)),
        "high": fmt(max(prices)),
        "change_pct": fmt(((last - first) / first * 100).quantize(Decimal("0.001"))),
        "points": [{"time": utc(time_ms), "price": fmt(price)} for time_ms, price in recent],
    }
