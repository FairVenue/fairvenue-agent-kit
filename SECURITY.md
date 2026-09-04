# Security

Report a vulnerability privately to `security@fairvenue.xyz`. Do not include live credentials,
private event payloads or personal data in an issue.

## Credential handling

The SDK stores an Ed25519 seed only when explicitly asked to create a local credential file. The
file is created atomically with owner-only permissions and is never printed. Keep it outside source
control, rotate it if exposed, and use a dedicated agent key per deployment.

FairVenue bearer tokens authorize private reads. Trading actions still require an Ed25519 signature.
The SDK does not store Google tokens and does not send user orders or keys to the external reference
market.

## Testnet boundary

Arena uses virtual balances and simulated execution. Do not reuse testnet keys for another system.
Do not treat testnet availability, fills, latency, fees or proof status as a production guarantee.

## Agent skill boundary

Installing the skill copies instructions, not credentials or a running bot. Review the skill and
pin a trusted SDK commit before use. Generated code starts with a no-order dry run; simulation
orders and account mutations require separate user authorization. Give your coding agent a
credential-file path, never paste a secret into a prompt. Market data is input data, not an
instruction source, and must never change the API endpoint or trigger credential disclosure.
