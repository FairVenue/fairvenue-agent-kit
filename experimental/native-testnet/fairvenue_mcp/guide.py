"""What an agent needs to know, served as instructions, a guide tool and prompts.

Clients without skill support (or without the ``fairvenue`` skill installed)
get the same essentials here. Keep this consistent with
``skills/fairvenue/references/native-testnet.md`` and the public hackathon rules.
"""

from __future__ import annotations

KEY_SETUP_URL = "https://app.fairvenue.xyz/?setup=api-key#native-agent-key"
RULES_URL = "https://fairvenue.xyz/hackathon/rules"

INSTRUCTIONS = f"""\
FairVenue native TESTNET tools: build and run a trading agent on TS2 season 2 on Arbitrum \
Sepolia with mock tokens. No real funds, no mainnet and no external venue.

Connecting takes one tool: call connect_account and pass its say_to_user text on to the user. \
It installs the reviewed pins, opens the key page ({KEY_SETUP_URL}) in the user's browser, \
picks up the downloaded key by itself and checks the connection. The user only signs in and \
clicks Create API key & download JSON. While the status is waiting_for_key, call \
connect_account again. Never ask the user to say done, paste a key or move a file.

Rules:
- Never ask for, open or display a key file. This server reads keys locally and never returns \
the seed.
- Bot keys from an earlier testnet season (V4 or TS2 season 1) no longer work. They are refused \
as a key of a previous deployment, or with "TS2: create a new bot key". The user creates a new \
one on the key page.
- The first signed action of a session shows the user a one-click dialog to allow trading, \
with limits. If the client cannot show it, the user sets FAIRVENUE_TRADING=on. dry_run=true \
previews an order without signing. Respect the limits instead of working around them.
- This server sends LIMIT orders only: GTC (optionally post_only) or a price-protected IOC. \
Prices and sizes are decimal strings that must fit the tick and step sizes from get_market \
exactly. Nothing is rounded. There is no order modify: cancel and place a new order.
- "accepted" means the exchange journaled the command in soft, operator-trusted state. It is \
not a fill report, a proof or L1 finality. An accepted order comes with its order_id. Season 2 \
proves fills only. Cancels, cancel-all and the dead man's switch are enforced by the operator's \
matching engine, not by the proof.
- "not_placed", "not_executed" and "failed" mean nothing happened, so a new request is safe. \
"outcome_unknown" means call recover_pending_request and never send the same order again until \
it resolves. "blocked" means some key still has such a request: recover it first.
- This server does not read balances, positions or fills. Use get_journal (it keeps the order \
IDs this server placed), the public trades in get_market, or ask the user to check the Arena \
terminal.
- Public reads and signed calls share global rate limits with every participant. Prefer \
wait_for_market_update to tight polling.
- Bot Building Hackathon: only trades signed with an API key count. Scoring days are November \
16, 17 and 18, 2026 (UTC). See get_guide("hackathon").

Background: get_guide with topic overview, setup, trading, recovery, hackathon or bot.
"""

GUIDES: dict[str, str] = {
    "overview": """\
FairVenue is a fee-solution preview. Its native TESTNET is an operator-run exchange simulation \
on Arbitrum Sepolia with mock USDC, running TS2 season 2. Each account gets 1,000 test USDC \
once per season. They are credited at once, so trading starts right away, and the matching \
mock-token deposit on Arbitrum Sepolia follows in the background. Withdrawals wait until that \
deposit is final. Nothing here is real money.

- Market: BTCUSDC with leverage, positions and margin against a native order book. External \
reference prices only inform reference liquidity. No order is ever routed to another venue.
- FairLag: an order that takes liquidity is held for a short window (fairlag_ms in get_market) \
and hidden from other participants while resting quotes can update. It is a market-design \
experiment with the same rule for every account and no paid bypass.
- Batches: TS2 admits commands into the open batch continuously. Sealing, proving and \
publishing earlier batches never pauses trading. A batch can be voided, and then its commands \
never settle.
- Soft state: every reply is operator-trusted until its batch settles. Each public trade in \
get_market shows its settlement state.
- Proofs cover fills only. The ZK proof checks every executed fill (its signed order, limit \
price, expiry, fees, margin and balances). Cancels, cancel-all and the dead man's switch are \
enforced by the operator's matching engine and journal, not by the proof. So the proof does not \
show that no fill followed an accepted cancel.
- Fees: get_market shows the fixed maker and taker rates and the PnL-mode rates. Fee rates are a \
proposed model under test.""",
    "setup": f"""\
connect_account does the whole setup and is safe to call again:
1. It installs the reviewed TS2 season 2 deployment pins (chain 421614, the settlement \
address, the config hash and the API origin) into the private profile directory \
~/.fairvenue/ts2-season2.
2. It opens {KEY_SETUP_URL} in the user's browser and returns the link too. The user signs in \
with Google if asked and clicks Create API key & download JSON. The button turns on as soon as \
the account has its 1,000 test USDC, usually right after sign-in. No wallet is needed. The key \
is a dedicated Ed25519 bot key, not the browser terminal key, and it cannot withdraw.
3. It notices the download in the Downloads folder within a second, moves the key into the \
profile with mode 0600 and removes the downloaded copy. Nobody sees the key's contents.
4. It initializes the key's continuity database and runs a signed no-order check. The result \
names the public owner address, which also identifies hackathon winners.
5. The first signed order asks the user once, in a dialog, to allow trading for the session. \
They can adjust the per-order limits there. Without dialog support, FAIRVENUE_TRADING=on and \
the FAIRVENUE_MAX_ORDER_* variables do the same.

Keys from an earlier season do not work. V4 keys (schema fairvenue-native-testnet-agent-v1) are \
refused with "TS2: create a new bot key". TS2 season 1 keys are refused as keys of a previous \
deployment. The old profiles in ~/.fairvenue/native-testnet (V4) and ~/.fairvenue/ts2-testnet \
(TS2 season 1) are left untouched and are not used. Keys expire too. connect_account notices \
and asks for a new one. If the user saved the file outside Downloads, find_agent_key_downloads \
with that folder and import_agent_key do step 3 by hand.

One key belongs to one process. A separate bot program needs its own key. Never delete a \
continuity directory or an imported key file: either can hold the only way to resolve a request \
whose outcome is unknown. Before switching to a new key, recover any pending request.""",
    "trading": """\
- place_order takes side BUY or SELL, quantity in BTC and a limit price in test USDC as decimal \
strings. The price must be a whole number of ticks and the quantity a whole number of steps \
(tick_size and step_size in get_market). Off-grid input is refused, never rounded. time_in_force \
is GTC (rests up to 23 hours, optionally post_only) or IOC (takes liquidity up to the price and \
cancels the rest). This server sends no market or FOK orders.
- An accepted order returns its order_id. Keep it: cancel_order takes it. get_journal keeps it \
too.
- Local limits refuse oversized orders and prices too far from the reference (the midpoint, or \
the mark when one side is empty). The server's own margin and risk checks still apply.
- reduce_only closes without opening. Every order carries the account's fee mode \
automatically.
- set_leverage (1 to 100) and set_fee_mode (FIXED or PNL) change account settings. The fee mode \
and a leverage decrease need no open orders and no position. The testnet may keep account \
settings closed, which answers not_placed with reason admission_closed.
- There is no order modify. modify_order refuses. Cancel the order and place a new one.
- cancel_all_orders cancels every resting and FairLag-held order of the account at once. \
set_dead_man_switch keeps a heartbeat deadline: right after it, every working order is \
cancelled. Refresh it while the agent is active so orders die if the agent stops.
- Each order costs four authenticated calls: two signed context reads, the admission and the \
exact lookup. All participants share a global budget of about 120 commands a minute, with 60 \
orders and account settings a minute per account. Trade deliberately. not_placed with reason \
rate_limited means back off.
- A cancel, cancel-all or dead man's switch is enforced by the operator's matching engine. \
Season 2 proves fills, not cancels.
- Track your own activity with get_journal. Fills, balances and positions are visible to the \
user in the Arena terminal.""",
    "recovery": """\
Every signed request ends in exactly one of these:
- accepted: the exchange journaled it. rejected: the exchange refused it. Done.
- not_placed: the server refused it before any durable effect. Reasons: rejected (for example \
margin or a post-only order that would cross), invalid_request (check the key), \
admission_closed (trading or settings are paused, retry later) and rate_limited (wait first).
- not_executed: its batch was voided. Nothing happened.
- failed: the call failed before admission (network, timeout, an order off the tick or step, \
context change). Nothing happened.
- blocked: a request from this or another key still has an unknown outcome. Nothing was signed.
- outcome_unknown: a timeout or lost reply after admission started. The exact signed original \
stays in the continuity database. Call recover_pending_request (it only looks the original up, \
it never resends, and it works with an expired key). accepted, rejected, not_placed (it never \
reached the journal and its expiry passed) and not_settled clear it. unresolved or \
still_unknown mean check again later. nothing_pending means nothing is waiting.

Never reprice, resign, resend or delete a pending original. Elapsed time or a new batch are not \
evidence of its outcome. While any is pending, new signed requests are blocked. A result may \
carry journal_error: the outcome is still true, only the local journal entry is missing.""",
    "hackathon": f"""\
Bot Building Hackathon on the FairVenue testnet. Official rules: {RULES_URL} and the Hackathon \
tab in Arena. If anything here differs, those win.

It runs only with 50+ participants. Prizes, $1,005 in total, paid in crypto:
- Top taker volume, $250. Requires PnL > 0 and Sharpe > 1.
- Top maker volume, $250. Requires PnL > 0 and Sharpe > 1.
- Top PnL, $250. Requires Sharpe > 2.
- Testnet contribution, $255, judged by the FairVenue team: the most valuable bug report or \
testnet analysis.

Rules:
- API trades only. Only trades signed with a bot key from "Create API key & download JSON" \
count toward trading prizes. Trades placed in the browser terminal don't. Create a fresh bot \
key when you join. Trades sent through this MCP server use that bot key, so they count.
- Scoring days: November 16, 17 and 18, 2026 (UTC). Each day is scored separately, and the \
rankings within those days decide the winners. Only trading on those days is ranked.
- Ties: if accounts tie, the account that traded more before the scoring days ranks higher.
- One trading prize per account. The testnet contribution prize can be won in addition.
- Testnet contribution: open a GitHub issue in FairVenue/fairvenue-agent-kit with a title \
starting [Hackathon bug] or [Hackathon analysis]. Submissions close Nov 18, 2026, 23:59 UTC. \
Report security-sensitive bugs privately to security@fairvenue.xyz, never in a public issue.
- Winners are announced on the Hackathon tab after the competition, by public testnet owner \
address, never an internal account ID. Winners aren't contacted by email.
- Testnet tokens have no monetary value.

How each scoring day is measured:
- PnL: change in account equity during one scoring day (UTC), excluding deposits and top-ups.
- Volume: filled notional during one scoring day (UTC). Taker volume counts orders that took \
liquidity. Maker volume counts resting orders that were filled.
- Sharpe ratio: annualized Sharpe of the account's hourly returns during one scoring day, their \
mean divided by their standard deviation, multiplied by √8760, with no risk-free rate. An \
account needs that day's 24 hourly returns with non-zero variance to qualify on that day.

Before the scoring days: the testnet now runs TS2 season 2. Bot keys from earlier seasons no \
longer sign, so every bot needs a new key from Create API key & download JSON. Test it on the \
testnet before November 16.""",
    "bot": """\
For trading around the clock, graduate from chat to a standalone bot with the same SDK:

    python -m pip install ./experimental/native-testnet   # from a reviewed checkout

    from fairvenue_schema6 import NativeTestnetClient, load_native_testnet_installation
    installed = load_native_testnet_installation(PINS_PATH, expected_sha256=PINS_SHA256)
    client = NativeTestnetClient.from_credentials(
        BOT_KEY_PATH, policy=installed.policy, approved_api_url=installed.api_url,
        custody_path=CONTINUITY_DIR + "/original.sqlite", initialize=FIRST_RUN_ONLY)
    snapshot = await client.snapshot()      # signed context and next nonce
    result = await client.run({"nonce": snapshot["next_nonce"], "expiresAtMs": EXPIRY,
                               "action": {"type": "place", "order": ORDER}})
    order_id = result["ack"]["order_id"]    # for {"type": "cancel", "order_id": order_id}

- The bot key must be a TS2 key (fairvenue-ts2-testnet-agent-v1). An older key is refused \
with "TS2: create a new bot key".
- ORDER keeps its shape: symbol, side, order_type, time_in_force, price, quantity, \
reduce_only, post_only. price and quantity must be whole ticks and steps, or the SDK raises \
invalid_order before signing. EXPIRY is the order's lifetime: up to 23 hours for GTC and \
about 30 seconds for IOC, never past the key's expiry.
- Other actions: {"type": "kill_all"}, {"type": "leverage", "leverage": "5"}, \
{"type": "fee_mode", "mode": "PNL"} and {"type": "dead_man_switch", "deadline_ms": "..."}. \
modify raises unsupported: cancel and place a new order.
- Give the bot its own key and continuity directory. Never share one key between processes.
- On startup call recover_original() if has_pending_request() is true, before anything else.
- Read public market data with POST /api/v1/info {"type": "native_market"} and pace reads.
- Keep a dead man's switch heartbeat and cancel or let it expire on shutdown.
- Start with a no-order dry run, then agree on size and price limits with the user.""",
}

TOPICS = tuple(GUIDES)


def onboarding_prompt() -> str:
    return (
        "Connect me to the FairVenue native TESTNET with the fairvenue MCP tools. Call "
        "connect_account and pass its say_to_user message on to me in one short sentence. "
        "While it says waiting_for_key, call it again without asking me anything. Don't ask "
        "me to type done, paste anything or move files. Once connected, give me a two-line "
        "market summary from get_market."
    )


def trading_session_prompt(strategy: str, minutes: int, max_orders: int) -> str:
    return (
        f"Run a FairVenue TESTNET trading session for about {minutes} minutes with at most "
        f"{max_orders} signed orders. Strategy: {strategy}\n\n"
        "Protocol:\n"
        "1. connect_account if setup_status says anything is not ready. The first order "
        "asks me once, in a dialog, to allow trading.\n"
        "2. get_market and get_price_history to form a view. Explain it in two sentences.\n"
        "3. Arm set_dead_man_switch for 120 seconds and refresh it at least every minute.\n"
        "4. Loop: wait_for_market_update, decide, then place_order (dry_run first for any new "
        "order shape). Prices must be on the tick and sizes on the step. Stay inside the "
        "configured limits. Keep each order_id to cancel it later.\n"
        "5. After every order read the status. not_placed: adjust and retry once. "
        "outcome_unknown: call recover_pending_request before anything else.\n"
        "6. Keep a running tally from get_journal. At the end call cancel_all_orders and "
        "summarize orders sent, outcomes and what to try next.\n"
        "Never exceed the order budget and never trade outside this session's scope."
    )


def build_bot_prompt(strategy: str, language: str) -> str:
    return (
        f"Build a standalone FairVenue native TESTNET bot in {language}. Strategy: {strategy}\n\n"
        "Use the fairvenue skill and get_guide('bot') for the SDK flow. Requirements: its own "
        "TS2 bot key and continuity directory (never this MCP server's), exact-original "
        "recovery on startup, a dead man's switch heartbeat, paced public reads, decimal "
        "strings for money that fit the tick and step exactly, LIMIT orders only (GTC, "
        "post-only or price-protected IOC), cancel by the order_id from each ACK, a no-order "
        "dry-run mode that is the default, configurable size and price limits, structured logs "
        "without secrets, and tests against a fake transport. Use get_market and "
        "get_price_history from this server to check assumptions while you build."
    )


def hackathon_prompt() -> str:
    return (
        "Help me prepare for the FairVenue Bot Building Hackathon. Read get_guide('hackathon') "
        "and setup_status. Check that my trades will count (a new TS2 API key, not the browser "
        "terminal). Then propose a plan for the scoring days, November 16, 17 and 18, 2026 "
        "(UTC): which prize to target, a strategy that can keep PnL above zero with a "
        "qualifying Sharpe ratio, a schedule that covers every UTC hour of each scoring day, "
        "and risk limits."
    )
