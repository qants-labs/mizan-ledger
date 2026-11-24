# Changelog

## 0.2.0 (prepared 2026-11-23, not yet released)
- New `mizan-ledger gate` subcommand (Mizan Gate): a CI calibration gate against a committed baseline.
  - Input: a ledger directory, or a JSONL file of `{id, confidence (0..1 or null), correct}` items. JSONL is converted
    to a ledger internally, so both share one scoring path.
  - Can fail the run (exit 1): Brier and the overconfident-wrong share (wrong at confidence >= 0.9, as a share of
    answered items), each only when even the best end of the 95% bootstrap interval is worse than the baseline by more
    than its threshold. A ledger that does not verify, or unreadable JSONL, also exits 1.
  - Report only: ECE (10 bins) and abstain rate (`null` confidence).
  - Below `--min-n` (default 50) answered items: "not enough data", no score, exit 0.
  - No baseline: one is created from the run, "inconclusive: baseline created", exit 0 (exit 2 with `--strict`).
  - Thresholds are configurable and labelled "QANTS starting values, not industry standards" in every report.
- Composite GitHub Action (`action.yml` at the repository root) and an example workflow in `examples/ci/`.
  - The baseline is saved to the Actions cache on default-branch pushes (`mizan-baseline-<agent-id>-<run id>`) and
    restored on pull requests (newest for the same agent id). A committed baseline file is used as is instead.
- Reports always state "answered N of M items". With ledger input the abstain rate is "not recorded (ledger format)",
  never 0.
- Core stays dependency-free and offline.
- Naming: the README headline, `server.json` titles and the Action name are "QANTS Mizan — agent calibration check", so
  this project is clearly distinct from the many unrelated MCP servers named "mizan".

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
