"""FairVenue native TESTNET MCP server over stdio.

Run ``fairvenue-mcp`` (or ``python -m fairvenue_mcp``). Configuration comes from
``FAIRVENUE_*`` environment variables. See ``docs/MCP.md``.

The user only clicks: ``connect_account`` opens the key page in the browser and
picks up the downloaded key itself, and the first signed action shows a
one-click dialog that allows trading for the session.

No ``from __future__ import annotations`` here: the confirmation parameter types
are local to ``create_server``, and the MCP SDK evaluates tool annotations.
"""

import argparse
import asyncio
import json
import math
import sys
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from decimal import Decimal
from typing import Annotated, Any, Literal, TypeVar

from mcp.server.elicitation import AcceptedElicitation, ElicitationResult
from mcp.server.mcpserver import Context, Elicit, MCPServer, Resolve
from mcp.server.mcpserver.exceptions import ToolError
from mcp_types import ToolAnnotations
from pydantic import BaseModel, Field

from . import __version__, guide
from .config import ConfigError, Limits, load_config
from .market import fmt
from .profile import SetupError
from .trading import (
    TRADING_DECLINED,
    TRADING_FORCED_OFF,
    TRADING_NEEDS_CONFIRMATION,
    ToolProblem,
    Trader,
)

T = TypeVar("T")
Topic = Literal["overview", "setup", "trading", "recovery", "hackathon", "bot"]

LOCAL_READ = ToolAnnotations(read_only_hint=True, open_world_hint=False)
PUBLIC_READ = ToolAnnotations(read_only_hint=True, open_world_hint=True)
#: A bot-signed read: it changes nothing on the exchange.
SIGNED_READ = ToolAnnotations(read_only_hint=True, open_world_hint=True)
LOCAL_SETUP = ToolAnnotations(
    read_only_hint=False, destructive_hint=False, idempotent_hint=True, open_world_hint=False
)
#: Setup that also runs a signed no-order read or a recovery lookup.
SIGNED_SETUP = ToolAnnotations(
    read_only_hint=False, destructive_hint=False, idempotent_hint=True, open_world_hint=True
)
SIGNED_ACTION = ToolAnnotations(
    read_only_hint=False, destructive_hint=True, idempotent_hint=False, open_world_hint=True
)

Side = Annotated[Literal["BUY", "SELL"], Field(description="BUY or SELL.")]
Quantity = Annotated[
    str,
    Field(
        description='Size in BTC as a decimal string, for example "0.001". Must be a whole '
        "number of steps (step_size in get_market). It is never rounded."
    ),
]
OrderType = Annotated[
    Literal["LIMIT", "MARKET"],
    Field(
        description="LIMIT needs a price. MARKET takes liquidity now and signs a worst price "
        "max_slippage_bps beyond the best opposite price (or the mark when that side is empty)."
    ),
]
Price = Annotated[
    str | None,
    Field(
        description="LIMIT only: the limit price in test USDC as a decimal string, for example "
        '"80100.5". Must be on the tick (tick_size in get_market). It is never rounded. Leave '
        "it empty for MARKET."
    ),
]
TimeInForce = Annotated[
    Literal["GTC", "IOC", "FOK"] | None,
    Field(
        description="GTC rests on the book (LIMIT only). IOC fills what it can now and cancels "
        "the rest. FOK fills completely or not at all. Default: GTC for LIMIT, IOC for MARKET."
    ),
]
PostOnly = Annotated[
    bool, Field(description="LIMIT GTC only. Refused locally if it would cross the book.")
]
ReduceOnly = Annotated[bool, Field(description="Only reduce an existing position.")]
Slippage = Annotated[
    Annotated[int, Field(ge=1, le=1000)] | None,
    Field(
        description="MARKET only: the worst price in basis points beyond the reference, 1 to "
        "1000. Default 50 (0.5%). It may not exceed the price-distance limit.",
    ),
]
DryRun = Annotated[bool, Field(description="Validate and show the exact order without signing.")]
OrderId = Annotated[
    str,
    Field(description="The 0x order ID from place_order, get_account or the Arena terminal."),
]


class TradingConsent(BaseModel):
    """The one-click dialog. Empty limits keep the values named in the message."""

    allow: bool = Field(default=True, title="Allow trading for this session")
    max_btc_per_order: float | None = Field(default=None, title="Max BTC per order")
    max_usdc_per_order: float | None = Field(default=None, title="Max test USDC per order")
    max_percent_from_market: float | None = Field(
        default=None, title="Max distance from the market price, in %"
    )


def _limit(value: float | None, default: Decimal, scale: int = 1) -> Decimal:
    if value is None or not math.isfinite(value) or value <= 0:
        return default
    return Decimal(str(value)) * scale


def create_server(trader: Trader | None, *, config_error: str | None = None) -> MCPServer[Any]:
    """The MCP surface. With a configuration error every tool reports it."""

    @asynccontextmanager
    async def lifespan(_: MCPServer[Any]) -> AsyncIterator[None]:
        try:
            yield None
        finally:
            if trader is not None:
                trader.close()

    server: MCPServer[Any] = MCPServer(
        "fairvenue",
        title="FairVenue native TESTNET",
        description="Account, market data, setup and signed trading for the FairVenue "
        "mock-token testnet.",
        instructions=guide.INSTRUCTIONS,
        website_url="https://github.com/FairVenue/fairvenue-agent-kit/blob/main/docs/MCP.md",
        version=__version__,
        lifespan=lifespan,
        log_level="WARNING",
    )

    def agent() -> Trader:
        if trader is None:
            raise ToolError(f"The FairVenue MCP server configuration is invalid: {config_error}")
        return trader

    async def run(work: Callable[[Trader], Awaitable[T]]) -> T:
        try:
            return await work(agent())
        except (ToolProblem, SetupError) as problem:
            raise ToolError(str(problem)) from None

    async def local(work: Callable[[Trader], T]) -> T:
        try:
            return work(agent())
        except (ToolProblem, SetupError) as problem:
            raise ToolError(str(problem)) from None

    # The trading confirmation -------------------------------------------------
    #
    # A resolver runs before the tool body. It either says no question is needed,
    # or returns an Elicit marker: the client then shows the user a dialog (mid-call
    # on protocol 2025-11-25 and older, as an input round trip on 2026-07-28), and
    # the tool body runs only once the user has answered.

    async def ask_trading_consent(ctx: Context[Any, Any]) -> Any:
        state = agent().trading_state()
        if state in ("on", "confirmed"):
            return "allowed"
        if state == "off":
            return "off"
        capabilities = ctx.client_capabilities
        elicitation = capabilities.elicitation if capabilities is not None else None
        if elicitation is None or (elicitation.form is None and elicitation.url is not None):
            return "no_dialog"
        limits = agent().config.limits
        return Elicit(
            "Allow this agent to sign orders and account settings on the FairVenue TESTNET for "
            "this session? Mock USDC only, no real funds. Each order: at most "
            f"{fmt(limits.max_order_quantity)} BTC and {fmt(limits.max_order_notional)} test "
            f"USDC, priced within {fmt(limits.max_price_deviation_bps / 100)}% of the market. "
            "Market orders stay within that distance too. Leave the limits empty to keep these "
            "values.",
            TradingConsent,
        )

    async def ask_unless_dry_run(dry_run: bool, ctx: Context[Any, Any]) -> Any:
        return "dry_run" if dry_run else await ask_trading_consent(ctx)

    Consent = Annotated[ElicitationResult[TradingConsent], Resolve(ask_trading_consent)]
    OrderConsent = Annotated[ElicitationResult[TradingConsent], Resolve(ask_unless_dry_run)]

    def apply_consent(consent: Any) -> None:
        if not isinstance(consent, AcceptedElicitation):
            raise ToolError(TRADING_DECLINED)
        answer = consent.data
        if isinstance(answer, TradingConsent):
            if not answer.allow:
                raise ToolError(TRADING_DECLINED)
            base = agent().config.limits
            limits = Limits(
                max_order_quantity=_limit(answer.max_btc_per_order, base.max_order_quantity),
                max_order_notional=_limit(answer.max_usdc_per_order, base.max_order_notional),
                max_price_deviation_bps=_limit(
                    answer.max_percent_from_market, base.max_price_deviation_bps, 100
                ),
            )
            try:
                agent().grant_consent(limits)
            except ToolProblem as problem:
                raise ToolError(str(problem)) from None
        elif answer == "off":
            raise ToolError(TRADING_FORCED_OFF)
        elif answer == "no_dialog":
            raise ToolError(TRADING_NEEDS_CONFIRMATION)

    # Setup ----------------------------------------------------------------------

    @server.tool(title="Connect account", annotations=SIGNED_SETUP)
    async def connect_account(
        wait_seconds: Annotated[
            float,
            Field(ge=5, le=55, description="How long to wait for the key download this call."),
        ] = 45,
    ) -> dict[str, Any]:
        """Connect the user's FairVenue TESTNET account. The one setup tool to call.

        Installs the reviewed pins, reuses a valid key, or opens the key page in
        the user's browser and picks up the downloaded key automatically, then
        checks the connection with a signed read that places no order. Relay
        say_to_user to the user. While the status is waiting_for_key, call it again.
        """
        return await run(lambda t: t.connect(wait_seconds=wait_seconds))

    @server.tool(title="Setup status", annotations=LOCAL_READ)
    async def setup_status() -> dict[str, Any]:
        """Local readiness, pending requests, the trading switch, limits and the next step.

        Reports the installed deployment pins, the active agent key (public fields
        only), its continuity database and any request with an unknown outcome.
        No network call.
        """
        if trader is None:
            return {"config_error": config_error, "next_steps": ["Ask the user to fix the config."]}
        return await run(lambda t: t.setup_status())

    @server.tool(title="Allow trading", annotations=LOCAL_SETUP)
    async def enable_trading(consent: Consent) -> dict[str, Any]:
        """Ask the user, in a one-click dialog, to allow trading for this session.

        Optional: the first signed action asks by itself. Returns the limits in force.
        """
        apply_consent(consent)
        return {"status": "allowed", **agent().limits_view()}

    @server.tool(title="Stop trading", annotations=LOCAL_SETUP)
    async def disable_trading() -> dict[str, Any]:
        """Stop signing orders until the user allows trading again."""
        return await local(lambda t: t.pause_trading())

    @server.tool(title="Find downloaded agent keys", annotations=LOCAL_READ)
    async def find_agent_key_downloads(
        extra_dirs: Annotated[
            list[str], Field(description="More directories to search besides the downloads folder.")
        ] = [],  # noqa: B006
    ) -> dict[str, Any]:
        """Find downloaded native TESTNET key files, for keys saved outside Downloads.

        Returns public fields only: agent public key, owner address, expiry and
        whether the key matches the installed deployment. Never the seed.
        """
        return await local(lambda t: t.find_downloads(extra_dirs))

    @server.tool(title="Import agent key", annotations=LOCAL_SETUP)
    async def import_agent_key(
        source_path: Annotated[str, Field(description="Path of the downloaded key file.")],
    ) -> dict[str, Any]:
        """Copy a downloaded key into the private profile (mode 0600) and make it active.

        connect_account does this by itself for keys in Downloads. The file is
        validated locally and its contents never reach the conversation. Refused
        while another key has a request with an unknown outcome. An expired key
        is accepted only to recover its own pending request. FAIRVENUE_KEY_FILE,
        when set, overrides the active key.
        """
        return await local(lambda t: t.import_key(source_path))

    # Account and market data ------------------------------------------------------

    @server.tool(title="Account", annotations=SIGNED_READ)
    async def get_account() -> dict[str, Any]:
        """Balances, margin, the position, open orders and recent fills of this account.

        A bot-signed read that places no order and needs no dialog: wallet balance,
        available and locked margin, realized and unrealized PnL, the position with
        its entry and mark price, fee mode and leverage, open and FairLag-held
        orders, the latest fills with their settlement state, and whether trading
        is open (with the reason in plain words when it is not). Soft,
        operator-trusted state.
        """
        return await run(lambda t: t.account())

    @server.tool(title="Market snapshot", annotations=PUBLIC_READ)
    async def get_market(
        depth: Annotated[int, Field(ge=1, le=20, description="Book levels per side.")] = 10,
        trades: Annotated[int, Field(ge=0, le=50, description="Recent public trades.")] = 10,
    ) -> dict[str, Any]:
        """Native BTCUSDC book, mark, midpoint, spread, recent trades, fees and rules.

        Public soft snapshot of the operator-run TS2 TESTNET, not proof-verified.
        Includes the tick and step sizes every order must fit exactly, and whether
        admission is open.
        """
        return await run(lambda t: t.market_snapshot(depth=depth, trades=trades))

    @server.tool(title="Price history", annotations=PUBLIC_READ)
    async def get_price_history(
        source: Annotated[
            Literal["book", "mark"],
            Field(description="book: native order book midpoints. mark: admitted oracle marks."),
        ] = "book",
        points: Annotated[int, Field(ge=2, le=2048, description="Most recent points.")] = 120,
    ) -> dict[str, Any]:
        """Recent native price history with low, high and change."""
        return await run(lambda t: t.history(source=source, points=points))

    @server.tool(title="Wait for market update", annotations=PUBLIC_READ)
    async def wait_for_market_update(
        timeout_seconds: Annotated[
            float, Field(ge=1, le=55, description="Give up after this many seconds.")
        ] = 30,
        after_protocol_time_ms: Annotated[
            str | None,
            Field(
                description="Wait for a snapshot newer than this protocol_time_ms. "
                "Without it, wait for any change in mark, book, trades or batch."
            ),
        ] = None,
    ) -> dict[str, Any]:
        """Block until the market changes, then return a compact snapshot.

        The event loop for trading agents: polls gently because the public lane
        is shared by every participant.
        """
        return await run(
            lambda t: t.wait_for_update(
                timeout_seconds=timeout_seconds,
                poll_seconds=3.0,
                after_protocol_time_ms=after_protocol_time_ms,
            )
        )

    # Signed actions -------------------------------------------------------------

    @server.tool(title="Place order", annotations=SIGNED_ACTION)
    async def place_order(
        side: Side,
        quantity: Quantity,
        consent: OrderConsent,
        order_type: OrderType = "LIMIT",
        price: Price = None,
        time_in_force: TimeInForce = None,
        post_only: PostOnly = False,
        reduce_only: ReduceOnly = False,
        max_slippage_bps: Slippage = None,
        dry_run: DryRun = False,
    ) -> dict[str, Any]:
        """Sign and submit one BTCUSDC order with the active bot key.

        LIMIT (GTC, post-only GTC, IOC or FOK) or MARKET (IOC or FOK with a worst
        price max_slippage_bps beyond the best opposite price). The first signed
        action of a session shows the user a one-click dialog to allow trading.
        Refuses prices off the tick and sizes off the step (nothing is rounded),
        enforces the size, notional and price-distance limits, and refuses
        locally while trading is paused. Status: accepted (journaled, with the
        order_id that cancel_order takes) or rejected, not_placed, not_executed or
        failed (nothing happened, a new request is safe), blocked (another request
        is pending) or outcome_unknown (call recover_pending_request, never resend).
        """
        apply_consent(consent)
        return await run(
            lambda t: t.place(
                dry_run=dry_run,
                side=side,
                quantity=quantity,
                price=price,
                order_type=order_type,
                time_in_force=time_in_force,
                post_only=post_only,
                reduce_only=reduce_only,
                max_slippage_bps=max_slippage_bps,
            )
        )

    @server.tool(title="Close position", annotations=SIGNED_ACTION)
    async def close_position(
        consent: OrderConsent,
        quantity: Annotated[
            str | None,
            Field(
                description="BTC to close as a decimal string, whole steps. Empty closes the "
                "whole position."
            ),
        ] = None,
        max_slippage_bps: Slippage = None,
        dry_run: DryRun = False,
    ) -> dict[str, Any]:
        """Close all or part of the position with a reduce-only MARKET IOC order.

        Reads the position with a signed account read, then signs a reduce-only
        order on the opposite side whose worst price is max_slippage_bps beyond
        the best opposite price (default 50 bps). It can only shrink the
        position, so the size and notional limits do not apply. The price
        distance limit does. Works while the mark lags. Shows the same statuses
        as place_order.
        """
        apply_consent(consent)
        return await run(
            lambda t: t.close_position(
                quantity=quantity, max_slippage_bps=max_slippage_bps, dry_run=dry_run
            )
        )

    @server.tool(title="Cancel order", annotations=SIGNED_ACTION)
    async def cancel_order(
        order_id: OrderId,
        consent: Consent,
    ) -> dict[str, Any]:
        """Cancel one order by its 0x order ID. place_order and get_account return it."""
        apply_consent(consent)
        return await run(lambda t: t.cancel(order_id=order_id))

    @server.tool(title="Cancel all orders", annotations=SIGNED_ACTION)
    async def cancel_all_orders(consent: Consent) -> dict[str, Any]:
        """Cancel every resting and FairLag-held order of the account at once (TS2 kill-all).

        Orders placed after it are not affected. It leaves the dead man's switch as
        it is. Cancels are still sent while trading is paused, the exchange decides.
        """
        apply_consent(consent)
        return await run(lambda t: t.cancel_all())

    @server.tool(title="Dead man's switch", annotations=SIGNED_ACTION)
    async def set_dead_man_switch(
        seconds: Annotated[
            int,
            Field(
                ge=0,
                le=86_000,
                description="Cancel all resting orders unless refreshed within this many "
                "seconds (minimum 2). 0 disarms it.",
            ),
        ],
        consent: Consent,
    ) -> dict[str, Any]:
        """Arm, refresh or disarm the account's dead man's switch heartbeat.

        Right after the deadline every resting and FairLag-held order is cancelled.
        """
        apply_consent(consent)
        return await run(
            lambda t: t.dead_man_switch(seconds=seconds, purpose="set_dead_man_switch")
        )

    @server.tool(title="Set leverage", annotations=SIGNED_ACTION)
    async def set_leverage(
        leverage: Annotated[
            int, Field(ge=1, le=100, description="Whole-number leverage, 1 to 100.")
        ],
        consent: Consent,
    ) -> dict[str, Any]:
        """Set the account's leverage. It can rise at any time. It can fall only with no
        position and no working orders. A refusal explains that rule. Asks for consent."""
        apply_consent(consent)
        return await run(lambda t: t.leverage(leverage=leverage))

    @server.tool(title="Set fee mode", annotations=SIGNED_ACTION)
    async def set_fee_mode(
        mode: Annotated[
            Literal["FIXED", "PNL"],
            Field(description="FIXED maker/taker rates, or PNL fees on profitable round trips."),
        ],
        consent: Consent,
    ) -> dict[str, Any]:
        """Switch the account's fee mode. Only with no position and no working orders.

        Every later order carries this mode. A refusal explains that rule. Asks for
        consent.
        """
        apply_consent(consent)
        return await run(lambda t: t.fee_mode(mode=mode))

    @server.tool(title="Recover pending request", annotations=SIGNED_SETUP)
    async def recover_pending_request() -> dict[str, Any]:
        """Look up the outcome of a signed request whose result is unknown.

        Works for any key, including an expired one, and returns the original's
        public action. Only queries the exact retained original and never resends
        it. accepted, rejected, not_placed (never journaled and expired) and
        not_settled resolve it. unresolved and still_unknown mean try again later.
        A request that never reached the server resolves only after its own
        expiry. nothing_pending means no request is waiting.
        """
        return await run(lambda t: t.recover())

    # Memory and knowledge -------------------------------------------------------

    @server.tool(title="Journal", annotations=LOCAL_READ)
    async def get_journal(
        limit: Annotated[int, Field(ge=1, le=200, description="Most recent entries.")] = 20,
    ) -> dict[str, Any]:
        """This server's own submitted requests and outcomes, newest last.

        The agent's memory of what it sent, including order IDs. For fills,
        balances and the position use get_account.
        """
        return await local(lambda t: {"entries": t.journal.tail(limit)})

    @server.tool(title="Guide", annotations=LOCAL_READ)
    async def get_guide(
        topic: Annotated[Topic, Field(description="Which guide to read.")] = "overview",
    ) -> str:
        """How the FairVenue TESTNET works: overview, setup, trading, recovery,
        hackathon rules, or building a standalone bot."""
        return guide.GUIDES[topic]

    # Resources and prompts ------------------------------------------------------

    def guide_reader(topic: str) -> Callable[[], str]:
        def read() -> str:
            return guide.GUIDES[topic]

        return read

    for topic in guide.TOPICS:
        server.resource(
            f"fairvenue://guide/{topic}",
            name=f"guide-{topic}",
            title=f"FairVenue guide: {topic}",
            mime_type="text/markdown",
        )(guide_reader(topic))

    @server.prompt(title="Set up FairVenue TESTNET trading")
    def onboard() -> str:
        """Connect in one step: the user only clicks in the browser."""
        return guide.onboarding_prompt()

    @server.prompt(title="Run a trading session")
    def trading_session(strategy: str, minutes: int = 30, max_orders: int = 20) -> str:
        """A bounded, autonomous trading loop with a dead man's switch."""
        return guide.trading_session_prompt(strategy, minutes, max_orders)

    @server.prompt(title="Build a standalone bot")
    def build_bot(strategy: str, language: str = "Python") -> str:
        """Turn a strategy into a standalone bot with its own key."""
        return guide.build_bot_prompt(strategy, language)

    @server.prompt(title="Hackathon plan")
    def hackathon_plan() -> str:
        """Check eligibility and plan the November 16 to 18 scoring days."""
        return guide.hackathon_prompt()

    return server


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="fairvenue-mcp",
        description="FairVenue native TESTNET MCP server (stdio). Configure with FAIRVENUE_* "
        "environment variables.",
    )
    parser.add_argument("--version", action="version", version=f"fairvenue-mcp {__version__}")
    parser.add_argument(
        "--check", action="store_true", help="Print local setup status as JSON and exit."
    )
    args = parser.parse_args(argv)
    try:
        config = load_config()
    except ConfigError as error:
        if args.check:
            print(f"fairvenue-mcp: {error}", file=sys.stderr)
            raise SystemExit(2) from None
        create_server(None, config_error=str(error)).run("stdio")
        return
    trader = Trader(config)
    if args.check:
        try:
            print(json.dumps(asyncio.run(trader.setup_status()), indent=2))
        finally:
            trader.close()
        return
    create_server(trader).run("stdio")
