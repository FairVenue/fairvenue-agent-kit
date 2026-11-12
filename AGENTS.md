# FairVenue Agent Kit engineering rules

- Keep the installable `skills/fairvenue/` bundle self-contained. It must not depend on sibling
  repository paths or the private backend checkout. Keep local skill reference links valid and
  installation instructions consistent with `FairVenue/fairvenue-agent-kit`.
- Skill installation is not permission to submit orders, create accounts, change fee modes or
  reset balances. Generated integrations must start in no-order dry-run mode.
- Do not publish this package to PyPI or choose a license without a separate explicit request.

- The optional MCP server (`experimental/native-testnet/fairvenue_mcp`) wraps only
  `fairvenue_schema6`; never edit the byte-identical protocol modules to suit it. Keep it local
  stdio. Never sign until the user accepts the trading dialog (an MCP elicitation the model
  cannot answer), clicks Allow on the one-shot loopback confirmation page (the fallback when the
  client has no form elicitation or declines/cancels in under 1.5 s), or the trading switch says
  on: `FAIRVENUE_TRADING`, else `{"trading": "on"}` in `~/.fairvenue/settings.json`. Read that file
  at every signed call and ignore it (fall back to ask, with a `setup_status` warning that never
  shows its contents) unless it is a regular, non-symlink file owned by the current user and not
  group- or world-writable. The server never writes it. The agent writes it only with the fixed
  command a refusal gives, after the user asked to trade, through the client's command approval.
  Keep dialog consent per server session. Never return, log or journal
  the confirmation page URL or token, and keep a slower decline a decline. Open only the reviewed
  https key page and that `127.0.0.1` page, with the desktop opener, never a console browser over
  the stdio channel. Never return, log or journal a
  seed, signature or signed envelope. Deployment pins come only from the reviewed packaged file
  and its SHA256, never from a key file or API reply. Keep `outcome_unknown` blocking until
  `recover_original()` resolves it; never resend, reprice or resign. Never reinitialize a
  continuity namespace that `state.json` records. Keep tool annotations accurate: reads are
  `readOnlyHint`, signing tools are `destructiveHint`.
- Keep the plugin manifests consistent: `.claude-plugin/plugin.json`, `.claude-plugin/marketplace.json`,
  the portable `plugin.json` and `mcp.json`, the native `pyproject.toml` version and
  `fairvenue_mcp.__version__`. Run `claude plugin validate .` after changing them.
- Hackathon facts in the skill and the MCP guide must match the public rules page.

- This SDK targets the FairVenue Arena virtual-settlement testnet. Never route user orders or
  credentials to Binance or another external venue.
- Keep monetary values as decimal strings or `Decimal`; reject binary floats in API payloads.
- Preserve RFC 8785 JCS, Ed25519 domain separation, unique per-agent nonces and short expiries.
- Never log private keys, bearer tokens, complete signed envelopes or private stream payloads.
- Local credential files must be owner-only and must never be committed.
- Treat `accepted` as durable receipt only, never as an open or filled order.
- No unbounded market-order abstraction. Taker helpers must submit a price-protected IOC.
- Book consumers must discard state and resubscribe after a sequence gap.
- Keep the checked-in signing vector compatible with the Rust server fixture.
- Before handoff run `ruff format --check .`, `ruff check .`, `mypy src`, `pytest`, and
  `python -m build`. With `./experimental/native-testnet[mcp]` installed also run `mypy` inside
  `experimental/native-testnet`, the native `unittest discover` suite and
  `python -m build experimental/native-testnet`.
