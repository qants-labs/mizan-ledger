# Changelog

## Unreleased (for 0.2.0; prepared 2026-11-23)
- Pre-action gate `mizan_ledger.gate.should_act(p, risk_threshold, ledger=None, agent_id=None)`: ACT only if
  p >= risk_threshold, otherwise ABSTAIN with a reason. With >= 50 resolved records, p is adjusted from the agent's
  reliability table (raw and adjusted both reported); with fewer, raw p is used and the reason says so. Optional
  `commit=True` declares the stated p first. New MCP tool `should_act` with the same behaviour (writes only when commit=true).

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
