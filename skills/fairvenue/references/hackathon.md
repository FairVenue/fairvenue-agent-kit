# Bot Building Hackathon

The official rules are on [fairvenue.xyz/hackathon/rules](https://fairvenue.xyz/hackathon/rules)
and the Hackathon tab in Arena. If anything here differs, those win. Everything runs on the
native TESTNET with mock tokens. Testnet tokens have no monetary value.

## Prizes

$1,005 in total, paid in crypto.

- **Top taker volume**, $250. Requires PnL > 0 and Sharpe > 1.
- **Top maker volume**, $250. Requires PnL > 0 and Sharpe > 1.
- **Top PnL**, $250. Requires Sharpe > 2.
- **Testnet contribution**, $255, judged by the FairVenue team: the most valuable bug report or
  testnet analysis.

## Rules

- **API trades only.** Only trades signed with a bot key from "Create API key & download JSON"
  count toward trading prizes. Trades placed in the browser terminal don't. Create a fresh bot
  key when you join. Trades signed by the MCP server or the native SDK with that key count.
- **Scoring days.** November 4, 5 and 6, 2026 (UTC). Each day is scored separately, and the
  rankings within those days decide the winners. Only trading on those days is ranked.
- **Ties.** If accounts tie, the account that traded more before the scoring days ranks higher.
- **One trading prize per account.** The testnet contribution prize can be won in addition.
- **Testnet contribution.** Open a GitHub issue in `FairVenue/fairvenue-agent-kit` with a title
  starting `[Hackathon bug]` or `[Hackathon analysis]`. Submissions close Nov 6, 2026, 23:59 UTC.
  Report security-sensitive bugs privately to security@fairvenue.xyz, never in a public issue.
- **Winners.** Announced on the Arena Hackathon tab after the competition, by public testnet
  owner address, never an internal account ID. Winners aren't contacted by email.

## How each scoring day is measured

- **PnL:** change in account equity during one scoring day (UTC), excluding deposits and
  top-ups.
- **Volume:** filled notional during one scoring day (UTC). Taker volume counts orders that took
  liquidity. Maker volume counts resting orders that were filled.
- **Sharpe ratio:** annualized Sharpe ratio of the account's hourly returns during one scoring
  day: their mean divided by their standard deviation, multiplied by √8760. No risk-free rate.
  An account needs that day's 24 hourly returns with non-zero variance to qualify for a trading
  prize on that day.

## Helping a participant

- Check that their trades will count: a fresh bot key from the setup page, not the browser
  terminal key.
- Pick one trading prize to target. Maker and taker volume both need PnL above zero and a Sharpe
  ratio above 1 on the day, so size risk for steady hourly returns rather than one large bet.
- Plan to cover every UTC hour of each scoring day, which usually means a standalone bot rather
  than a chat session.
- Use the testnet before November 4 to test the bot. Trading before the scoring days also
  breaks ties.
