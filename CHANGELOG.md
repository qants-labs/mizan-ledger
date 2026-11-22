# Changelog

## 0.2.0 (prepared 2026-11-22, not yet released)
- Adds `Ledger.void(task_id, reason, evidence=None)`: closes a declaration with outcome `"void"` and a required reason. A void is final, reveals the opening, is never scored, and still counts against coverage, so voiding cannot hide wrong answers. A void cannot be resolved later, and a resolved task cannot be voided.
- Adds optional `evidence` to `resolve()` and `void()`: a flat object (strings, numbers, booleans, null) describing what the resolver read, e.g. source, value read, read time and a hash of the raw response. It is sealed into the record hash, so editing it breaks `verify`.
- `summary()` and `verify` now report `voided` next to `declared`, `resolved` and `pending`. `resolved` counts scored outcomes only.
- `verify` rejects a void without a reason and a void that carries a score.
- Backward compatible: commitments, receipts and v0.1 resolution records are unchanged, and existing ledgers verify as before.

## 0.1.5 (prepared 2026-11-18, not yet released)
- Adds an `mcp-name` marker (`io.github.qants-labs/mizan-ledger`) so the MCP Registry can verify ownership under the QANTS organization namespace.
- Adds a `mizan-ledger mcp` subcommand that starts the same optional stdio MCP server as the existing `mizan-ledger-mcp` script (requires `pip install "mizan-ledger[mcp]"`). If the extra is missing, the command prints an install hint and exits nonzero.

## 0.1.4 (2026-11-18)
- Metadata only. No code or behaviour change from 0.1.3.
- Restores a Source URL now that the repository lives under the QANTS organization: `https://github.com/qants-labs/mizan-ledger`.
- Adds an Issues URL: `https://github.com/qants-labs/mizan-ledger/issues`.
- Homepage, Documentation and SeedAgents URLs are unchanged (official QANTS site).

## 0.1.3 (2026-11-18)
- Metadata only. No code or behaviour change from 0.1.2.
- Project URLs now point only to the official QANTS site: Homepage `https://qants-site.qants.workers.dev/library`,
  Documentation `https://qants-site.qants.workers.dev/library.md`, SeedAgents `https://qants-site.qants.workers.dev/seed-agents`.
  The personal GitHub "Source" link was removed.
- Removed the `mcp-name` marker from the README and the MCP server docstring (no MCP Registry entry used it).

## 0.1.2 (2026-11-17)
- Optional local stdio MCP server (`[mcp]` extra), project URLs.

## 0.1.1 (2026-11-16)
- See PyPI release history.
