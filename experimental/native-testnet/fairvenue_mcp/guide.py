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
connect_account again. Never ask the user to say done, paste a key or move a file, unless the \
browser runs on another machine (then the user moves the key file and you call \
import_agent_key).

Rules:
- Never ask for, open or display a key file. This server reads keys locally and never returns \
the seed.
- Bot keys from an earlier testnet season (V4 or TS2 season 1) no longer work. They are refused \
as a key of a previous deployment, or with "TS2: create a new bot key". The user creates a new \
one on the key page.
- The first signed action of a session shows the user a one-click dialog to allow trading, \
with limits. If the client cannot show it, the user sets FAIRVENUE_TRADING=on. dry_run=true \
previews an order without signing. Respect the limits instead of working around them.
- get_account shows balances, margin, the position, open orders and recent fills. Read it \
before and after trading.
- Orders: LIMIT (GTC, post-only GTC, IOC or FOK) or MARKET (IOC or FOK). A MARKET order signs \
a worst price max_slippage_bps beyond the best opposite price (default 50). close_position \
closes all or part of the position with a reduce-only MARKET order. Prices and sizes are \
decimal strings that must fit the tick and step sizes from get_market exactly. Nothing is \
rounded. There is no order modify: cancel and place a new order.
- "accepted" means the exchange journaled the command in soft, operator-trusted state. It is \
not a fill report, a proof or L1 finality. An accepted order comes with its order_id. Season 2 \
proves fills only. Cancels, cancel-all and the dead man's switch are enforced by the operator's \
matching engine, not by the proof.
- "not_placed", "not_executed" and "failed" mean nothing happened, so a new request is safe. \
not_placed with reason admission_closed means trading is paused, and the result says why in \
plain words. "outcome_unknown" means call recover_pending_request and never send the same \
order again until it resolves. "blocked" means some key still has such a request: recover it \
first.
- Public reads and signed calls share global rate limits with every participant. Prefer \
wait_for_market_update to tight polling.
- Bot Building Hackathon: only trades signed with an API key count. Scoring days are November \
16, 17 and 18, 2026 (UTC). Prizes need a full day of hourly returns, so a standalone bot \
usually beats a chat session. See get_guide("hackathon").

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
experiment with the same rule for every account and no paid bypass. get_account counts such \
held orders without showing them.
- Batches: TS2 admits commands into the open batch continuously. Sealing, proving and \
publishing earlier batches never pauses trading. A batch can be voided, and then its commands \
never settle.
- Soft state: every reply is operator-trusted until its batch settles. Each public trade in \
get_market and each fill in get_account shows its settlement state.
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

WSL: the browser saves to Windows, so set FAIRVENUE_DOWNLOAD_DIRS to \
/mnt/c/Users/<you>/Downloads in the server environment. Remote or SSH machines: set \
FAIRVENUE_OPEN_BROWSER=0, open the link on your own computer, then copy the downloaded key to \
the remote machine (for example with scp, into a private folder) and call import_agent_key \
with its path, or find_agent_key_downloads with that folder.

Keys from an earlier season do not work. V4 keys (schema fairvenue-native-testnet-agent-v1) are \
refused with "TS2: create a new bot key". TS2 season 1 keys are refused as keys of a previous \
deployment. The old profiles in ~/.fairvenue/native-testnet (V4) and ~/.fairvenue/ts2-testnet \
(TS2 season 1) are left untouched and are not used. Keys expire too. connect_account notices \
and asks for a new one.

One key belongs to one process. A separate bot program needs its own key. Never delete a \
continuity directory or an imported key file: either can hold the only way to resolve a request \
whose outcome is unknown. Before switching to a new key, recover any pending request.""",
    "trading": """\
- get_account first: balances, available margin, the position with unrealized PnL, open \
orders, FairLag-held orders and recent fills with their settlement state.
- place_order takes side BUY or SELL and quantity in BTC as a decimal string. order_type \
LIMIT needs a price in test USDC that is a whole number of ticks. The quantity must be a whole \
number of steps (tick_size and step_size in get_market). Off-grid input is refused, never \
rounded. time_in_force is GTC (rests, optionally post_only), IOC (fills what it can now and \
cancels the rest) or FOK (fills completely or not at all). A GTC order rests 23 hours by \
default (FAIRVENUE_GTC_LIFETIME_MINUTES), never past the key's expiry.
- order_type MARKET takes no price. It is IOC (default) or FOK and signs a worst price \
max_slippage_bps beyond the best opposite price, or beyond the mark when that side is empty. \
The default is 50 bps (0.5%, the Arena terminal's bound). It may not exceed the price-distance \
limit.
- close_position closes the whole position, or the quantity you name, with a reduce-only \
MARKET IOC. It can only shrink the position, so the size and notional limits do not apply.
- An accepted order returns its order_id. Keep it: cancel_order takes it. get_account and \
get_journal list it too.
- Local limits refuse oversized orders and prices too far from the reference (the midpoint, or \
the mark when one side is empty). The server's own margin and risk checks still apply.
- While trading is paused (admission closed) nothing is signed except cancels, cancel-all and, \
while the mark catches up, reduce-only orders. The refusal says why in plain words. During a \
funding payment only cancels work.
- reduce_only closes without opening. Every order carries the account's fee mode \
automatically.
- set_leverage (1 to 100) and set_fee_mode (FIXED or PNL) change account settings and ask for \
consent like orders. Leverage can rise at any time. It can fall only with no position and no \
working orders. The fee mode changes only with no position and no working orders. A refusal \
says so.
- There is no order modify. Cancel the order and place a new one.
- cancel_all_orders cancels every resting and FairLag-held order of the account at once. \
set_dead_man_switch keeps a heartbeat deadline: right after it, every working order is \
cancelled. Refresh it while the agent is active so orders die if the agent stops.
- Each order costs three signed calls: one context read, the admission and the exact lookup. \
get_account costs two signed reads. All participants share a global budget of about 120 \
commands a minute, with 60 orders and account settings a minute per account. Trade \
deliberately. not_placed with reason rate_limited means back off.
- A cancel, cancel-all or dead man's switch is enforced by the operator's matching engine. \
Season 2 proves fills, not cancels.""",
    "recovery": """\
Every signed request ends in exactly one of these:
- accepted: the exchange journaled it. rejected: the exchange refused it. Done.
- not_placed: nothing was placed. Reasons: rejected (for example margin or a post-only order \
that would cross), invalid_request (check the key), admission_closed (trading is paused, the \
result says why, retry later) and rate_limited (wait first).
- not_executed: its batch was voided. Nothing happened.
- failed: the call failed before admission (network, timeout, an order off the tick or step, \
context change). Nothing happened.
- blocked: a request from this or another key still has an unknown outcome. Nothing was signed.
- outcome_unknown: a timeout or lost reply after admission started. The exact signed original \
stays in the continuity database. Call recover_pending_request (it only looks the original up, \
it never resends, and it works with an expired key). accepted, rejected, not_placed (it never \
reached the journal and its expiry passed) and not_settled clear it. unresolved or \
still_unknown mean check again later. nothing_pending means nothing is waiting.

If a request was stored but never reached the server, its lookup stays unresolved until the \
request's own expiry has passed: 30 seconds for IOC, FOK, MARKET and account commands, the \
GTC lifetime (23 hours by default) for a resting order. Signing waits until then. Keep calling \
recover_pending_request.

Never reprice, resign, resend or delete a pending original. Elapsed time or a new batch are not \
evidence of its outcome. Only the server's lookup answer is. While any is pending, new signed \
requests are blocked. A result may carry journal_error: the outcome is still true, only the \
local journal entry is missing.""",
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

For a prize, run a standalone bot: a chat session rarely covers all 24 hours of a scoring day. \
Use this server to explore, test and build that bot (get_guide("bot")), then give the bot its \
own key.

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
    account = await client.account()         # signed read: balances, position, orders, fills
    result = await client.submit({"type": "place", "order": ORDER}, lifetime_ms=LIFETIME_MS)
    order_id = result["ack"]["order_id"]    # for {"type": "cancel", "order_id": order_id}

- The bot key must be a season 2 TS2 key (fairvenue-ts2-testnet-agent-v1). An older key is \
refused.
- ORDER: symbol, side, order_type, time_in_force, price, quantity, reduce_only, post_only. \
price and quantity must be whole ticks and steps, or the SDK raises invalid_order before \
signing. A MARKET order (IOC or FOK) with price None signs a worst price max_slippage_bps \
(1 to 1000, default 50) beyond the best opposite price, or beyond the mark. LIFETIME_MS is up \
to 23 hours for GTC and 30 seconds for one-shot orders and account commands.
- submit reads the signed context once for its nonce and expiry. While trading is paused it \
refuses locally with admission_closed, except cancels, kill-all and (while the mark lags) \
reduce-only orders. client.last_context() says why.
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
        "summary of my account from get_account and of the market from get_market."
    )


def trading_session_prompt(strategy: str, minutes: int, max_orders: int) -> str:
    return (
        f"Run a FairVenue TESTNET trading session for about {minutes} minutes with at most "
        f"{max_orders} signed orders. Strategy: {strategy}\n\n"
        "Protocol:\n"
        "1. connect_account if setup_status says anything is not ready. The first order "
        "asks me once, in a dialog, to allow trading.\n"
        "2. get_account, get_market and get_price_history to form a view. Explain it in two "
        "sentences.\n"
        "3. Arm set_dead_man_switch for 120 seconds and refresh it at least every minute.\n"
        "4. Loop: wait_for_market_update, decide, then place_order (dry_run first for any new "
        "order shape). Prices must be on the tick and sizes on the step. MARKET orders keep "
        "max_slippage_bps small. Stay inside the configured limits. Keep each order_id.\n"
        "5. After every order read the status. not_placed: read the reason, adjust and retry "
        "once. outcome_unknown: call recover_pending_request before anything else. Check fills "
        "and the position with get_account.\n"
        "6. At the end call cancel_all_orders, close_position if I hold one, and summarize "
        "orders, fills, PnL from get_account and what to try next.\n"
        "Never exceed the order budget and never trade outside this session's scope."
    )


def build_bot_prompt(strategy: str, language: str) -> str:
    return (
        f"Build a standalone FairVenue native TESTNET bot in {language}. Strategy: {strategy}\n\n"
        "Use the fairvenue skill and get_guide('bot') for the SDK flow. Requirements: its own "
        "TS2 season 2 bot key and continuity directory (never this MCP server's), "
        "exact-original recovery on startup, a dead man's switch heartbeat, paced public reads, "
        "account reads with client.account() to track fills and the position, decimal strings "
        "for money that fit the tick and step exactly, LIMIT or slippage-bounded MARKET orders, "
        "cancel by the order_id from each ACK, a no-order dry-run mode that is the default, "
        "configurable size and price limits, structured logs without secrets, and tests "
        "against a fake transport. Use get_market and get_price_history from this server to "
        "check assumptions while you build."
    )


def hackathon_prompt() -> str:
    return (
        "Help me prepare for the FairVenue Bot Building Hackathon. Read get_guide('hackathon') "
        "and setup_status. Check that my trades will count (a new season 2 API key, not the "
        "browser terminal). Then propose a plan for the scoring days, November 16, 17 and 18, "
        "2026 (UTC): which prize to target, a strategy that can keep PnL above zero with a "
        "qualifying Sharpe ratio, a standalone bot that covers every UTC hour of each scoring "
        "day, and risk limits."
    )
