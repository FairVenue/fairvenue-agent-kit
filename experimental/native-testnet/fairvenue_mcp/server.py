"""FairVenue native TESTNET MCP server over stdio.

Run ``fairvenue-mcp`` (or ``python -m fairvenue_mcp``). Configuration comes from
``FAIRVENUE_*`` environment variables; see ``docs/MCP.md``.

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
LOCAL_SETUP = ToolAnnotations(
    read_only_hint=False, destructive_hint=False, idempotent_hint=True, open_world_hint=False
)
SIGNED_READ = ToolAnnotations(
    read_only_hint=False, destructive_hint=False, idempotent_hint=True, open_world_hint=True
)
SIGNED_ACTION = ToolAnnotations(
    read_only_hint=False, destructive_hint=True, idempotent_hint=False, open_world_hint=True
)

Side = Annotated[Literal["BUY", "SELL"], Field(description="BUY or SELL.")]
Quantity = Annotated[
    str, Field(description='Size in BTC as a decimal string, for example "0.001".')
]
Price = Annotated[
    str,
    Field(
        description='Limit price in test USDC as a decimal string, for example "80100.5". '
        "IOC orders use it as their worst acceptable price."
    ),
]
TimeInForce = Annotated[
    Literal["GTC", "IOC"],
    Field(description="GTC rests on the book. IOC takes liquidity up to the price, then cancels."),
]
PostOnly = Annotated[
    bool, Field(description="GTC only. Refused locally if it would cross the book.")
]
ReduceOnly = Annotated[bool, Field(description="Only reduce an existing position.")]
DryRun = Annotated[
    bool, Field(description="Validate, round and show the exact order without signing it.")
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
        description="Market data, setup and signed trading for the FairVenue mock-token testnet.",
        instructions=guide.INSTRUCTIONS,
        website_url="https://fairvenue.xyz/docs",
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
            "Allow this agent to sign orders on the FairVenue TESTNET for this session? Mock "
            "USDC only, no real funds. Each order: at most "
            f"{fmt(limits.max_order_quantity)} BTC and {fmt(limits.max_order_notional)} test "
            f"USDC, priced within {fmt(limits.max_price_deviation_bps / 100)}% of the market. "
            "Leave the limits empty to keep these values.",
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

    @server.tool(title="Connect account", annotations=SIGNED_READ)
    async def connect_account(
        wait_seconds: Annotated[
            float,
            Field(ge=5, le=55, description="How long to wait for the key download this call."),
        ] = 45,
    ) -> dict[str, Any]:
        """Connect the user's FairVenue TESTNET account. The one setup tool to call.

        Installs the reviewed pins, reuses a valid key, or opens the key page in
        the user's browser and picks up the downloaded key automatically, then
        checks the connection without placing an order. Relay say_to_user to the
        user. While the status is waiting_for_key, call it again.
        """
        return await run(lambda t: t.connect(wait_seconds=wait_seconds))

    @server.tool(title="Setup status", annotations=LOCAL_READ)
    async def setup_status() -> dict[str, Any]:
        """Local readiness, pending requests, the trading switch and the next step.

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

    @server.tool(title="Install deployment pins", annotations=LOCAL_SETUP)
    async def install_deployment_pins() -> dict[str, Any]:
        """Install the reviewed TESTNET deployment pins. connect_account does this too.

        Copies the pins packaged with this server after checking their SHA256.
        Never overwrites an existing file.
        """
        return await local(lambda t: t.install_pins())

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

    @server.tool(title="Verify connection", annotations=SIGNED_READ)
    async def verify_connection() -> dict[str, Any]:
        """Signed no-order check of the active key against the TESTNET API.

        Returns the public owner address, key expiry, batch and whether a request
        is pending.
        """
        return await run(lambda t: t.verify())

    # Market data ----------------------------------------------------------------

    @server.tool(title="Market snapshot", annotations=PUBLIC_READ)
    async def get_market(
        depth: Annotated[int, Field(ge=1, le=20, description="Book levels per side.")] = 10,
        trades: Annotated[int, Field(ge=0, le=50, description="Recent public trades.")] = 10,
    ) -> dict[str, Any]:
        """Native BTCUSDC book, mark, midpoint, spread, recent trades, fees and rules.

        Public soft snapshot of the operator-run TESTNET, not proof-verified.
        Includes tick and step sizes needed to price orders.
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
        price: Price,
        consent: OrderConsent,
        time_in_force: TimeInForce = "GTC",
        post_only: PostOnly = False,
        reduce_only: ReduceOnly = False,
        dry_run: DryRun = False,
    ) -> dict[str, Any]:
        """Sign and submit one LIMIT order for BTCUSDC with the active bot key.

        The first signed action of a session shows the user a one-click dialog to
        allow trading. Rounds to tick and step, enforces the limits and refuses
        prices far from the reference. Status: accepted or rejected (the engine
        executed or refused it), not_placed, not_executed or failed (nothing
        happened, a new request is safe), blocked (another request is pending)
        or outcome_unknown (call recover_pending_request, never resend).
        """
        apply_consent(consent)
        return await run(
            lambda t: t.place(
                dry_run=dry_run,
                side=side,
                quantity=quantity,
                price=price,
                time_in_force=time_in_force,
                post_only=post_only,
                reduce_only=reduce_only,
            )
        )

    @server.tool(title="Modify order", annotations=SIGNED_ACTION)
    async def modify_order(
        order_id: Annotated[str, Field(description="Order ID from the Arena terminal.")],
        side: Side,
        quantity: Quantity,
        price: Price,
        consent: OrderConsent,
        time_in_force: TimeInForce = "GTC",
        post_only: PostOnly = False,
        reduce_only: ReduceOnly = False,
        dry_run: DryRun = False,
    ) -> dict[str, Any]:
        """Atomically cancel one resting order and submit its replacement."""
        apply_consent(consent)
        return await run(
            lambda t: t.modify(
                order_id=order_id,
                dry_run=dry_run,
                side=side,
                quantity=quantity,
                price=price,
                time_in_force=time_in_force,
                post_only=post_only,
                reduce_only=reduce_only,
            )
        )

    @server.tool(title="Cancel order", annotations=SIGNED_ACTION)
    async def cancel_order(
        order_id: Annotated[str, Field(description="Order ID from the Arena terminal.")],
        consent: Consent,
    ) -> dict[str, Any]:
        """Cancel one resting order by ID. Bot keys cannot list order IDs: ask the
        user to copy it from the Arena terminal, or use cancel_all_orders."""
        apply_consent(consent)
        return await run(lambda t: t.cancel(order_id=order_id))

    @server.tool(title="Cancel all orders", annotations=SIGNED_ACTION)
    async def cancel_all_orders(
        consent: Consent,
        within_seconds: Annotated[
            int, Field(ge=2, le=600, description="Deadline from now, at least 2 seconds.")
        ] = 2,
    ) -> dict[str, Any]:
        """Cancel every resting order of the account via the dead man's switch.

        Takes effect at the first oracle observation after the deadline,
        roughly 10 to 20 seconds. Replaces any earlier switch deadline.
        """
        apply_consent(consent)
        return await run(
            lambda t: t.dead_man_switch(seconds=within_seconds, purpose="cancel_all_orders")
        )

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
        """Arm, refresh or disarm the account's dead man's switch heartbeat."""
        apply_consent(consent)
        return await run(
            lambda t: t.dead_man_switch(seconds=seconds, purpose="set_dead_man_switch")
        )

    @server.tool(title="Set leverage", annotations=SIGNED_ACTION)
    async def set_leverage(
        leverage: Annotated[int, Field(ge=1, le=1000, description="Whole-number leverage.")],
        consent: Consent,
    ) -> dict[str, Any]:
        """Set the account's leverage. The engine refuses values it does not allow."""
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
        """Switch the account's fee mode. Only allowed with no open orders or position."""
        apply_consent(consent)
        return await run(lambda t: t.fee_mode(mode=mode))

    @server.tool(title="Recover pending request", annotations=SIGNED_READ)
    async def recover_pending_request() -> dict[str, Any]:
        """Look up the outcome of a signed request whose result is unknown.

        Works for any key, including an expired one, and returns the original's
        public action and nonce. Only queries the exact retained original and
        never resends it. accepted, rejected, not_admitted and not_settled
        resolve it. unresolved, bound_incomplete and still_unknown mean try again
        later. nothing_pending means no request is waiting.
        """
        return await run(lambda t: t.recover())

    # Memory and knowledge -------------------------------------------------------

    @server.tool(title="Journal", annotations=LOCAL_READ)
    async def get_journal(
        limit: Annotated[int, Field(ge=1, le=200, description="Most recent entries.")] = 20,
    ) -> dict[str, Any]:
        """This server's own submitted requests and outcomes, newest last.

        The agent's memory: bot keys cannot read orders, fills or balances.
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
        """Check eligibility and plan the November 4 to 6 scoring days."""
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
