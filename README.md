# mizan-ledger

<!-- mcp-name: io.github.qants-labs/mizan-ledger -->

Declare how confident your agent is **before** a task, commit a salted hash of that declaration, then score honesty against the **real outcome** (your own tests, evals or checks). Local-first, hash-only, verifiable offline.

> Status: **v0.1.5, early alpha** by QANTS. On PyPI (not on npm). MIT licensed (see `LICENSE`). No network access, no telemetry, no dependencies in the core library.

## Why

Agents often report success with the same unearned confidence whether they are right or wrong. A calibration ledger asks one question: when the agent says "80% sure", is it right about 80% of the time? Brier score and ECE answer that. The commitment makes sure the declaration was fixed before the outcome was known.

## Install

```
pip install mizan-ledger
```

Or from a local checkout:

```bash
pip install -e .            # Python >= 3.9, pytest only for development
```

## 30-second example

```python
from mizan_ledger import Ledger

led = Ledger("ledger/", private_path="private/")          # public dir is safe to share; private dir is not
led.declare("fix-bug-421", 0.80, agent_id="my-agent", version="1.2.0")   # hash-only receipt, timestamped
# ... run the task, run the tests ...
led.resolve("fix-bug-421", outcome=True)                  # reveals the opening, scores it

print(led.summary("my-agent"))     # n, Brier + 95% interval, ECE, log loss, accuracy (Wilson), coverage
```

```bash
mizan-ledger verify ledger/        # offline check of hash chains, openings and scores
```

## What gets recorded

| File | Visibility | Contents |
|---|---|---|
| `receipts.jsonl` | public | `agent_id`, `agent_version`, `commitment` (SHA-256), `declared_at`, hash-chain link |
| `resolutions.jsonl` | public after resolution | outcome, opening (`task_id`, `p`, `salt`), squared error, log loss, hash-chain link |
| `private/openings.jsonl` | **private** (mode 600) | the same openings, kept until the task is resolved |

Commitment = `SHA-256(canonical_json({agent_id, agent_version, p, salt, schema, task_id}))` with `p` as a fixed 6-decimal string, a fresh 128-bit random salt per declaration, sorted keys and no whitespace. Shared test vectors (`vectors/commitments.json`) are checked by both the Python and the JavaScript implementation.

## Guarantees and honest limits

- A declaration cannot be replaced or re-resolved. Deleting or editing a record breaks the hash chain and `verify` fails.
- **Coverage is reported.** Declared, resolved and pending are all counted, so you cannot quietly drop the bad ones. Anything unresolved shows as `pending`.
- **Timestamps are your local clock.** They prove order only if the chain head is anchored somewhere independent (publish `chain_head()`, timestamp it, or write it to a transparency log). Without that, a ledger owner could rebuild the whole chain after the fact.
- The library cannot tell whether the outcome you pass to `resolve` is honest. Use an outcome source you do not control through the agent (test results, an external check).
- Small samples give wide intervals; ECE is biased upward for small n. The summary prints the interval and `n`; read them.
- Scores are about calibration, not about whether the agent is good, safe or secure.

## Pre-action gate (`should_act`)

Put one check in front of anything irreversible (delete, send, pay). The agent states its confidence; the gate returns
`ACT` only if it meets your risk threshold, otherwise `ABSTAIN` with a reason.

```python
from mizan_ledger import Ledger
from mizan_ledger.gate import should_act

led = Ledger(".mizan")
d = should_act(p=0.82, risk_threshold=0.95, ledger=led, agent_id="my-agent",
               task_id="delete-old-backups-2026-11-23", commit=True)   # commit: the stated p is declared first
if not d.act:
    raise SystemExit(f"abstain: {d.reason}")                            # ask a human instead
delete_old_backups()
led.resolve("delete-old-backups-2026-11-23", outcome=check_backups_ok())
```

- With at least 50 resolved records for the agent, `p` is adjusted with the agent's own reliability table (the success
  rate in `p`'s bin, shrunk toward `p` when the bin is small). Both `p_raw` and `p_adjusted` are returned, and the
  decision uses the adjusted value. With fewer records, raw `p` is used and `reason` says so.
- `commit=True` declares the stated `p` before the action, so the task is scored whether the agent acted or abstained.
  The ledger stores the stated `p` only; the decision is returned to you.
- The same check is the `should_act` tool of the optional MCP server.
- It relies on stated confidence and past calibration. It cannot verify the agent's reasoning, and a well-calibrated
  agent can still be wrong.

## Scores (`mizan_ledger.metrics`)

Brier (mean squared error), log loss (p clipped to [0.01, 0.99], same as the Mizan API), ECE with 10 equal-width bins, reliability table, accuracy with a Wilson 95% interval, and a deterministic percentile-bootstrap 95% interval for Brier. The always-0.5 baseline Brier is 0.25.

## Adapters

| Adapter | Status |
|---|---|
| **pytest plugin** (`mizan_ledger.pytest_plugin`): `@pytest.mark.confidence(0.9)` declares before the test body, pass/fail resolves it. Run with `pytest --mizan-ledger-dir .mizan --mizan-agent-id my-agent`. | Tested here (pytester). |
| **Generic trace evaluator** (`mizan_ledger.adapters.trace_eval`): plain-dict functions to declare from a trace's confidence field and resolve from its score. | Unit-tested with synthetic dicts only. **Not tested against Arize Phoenix, Braintrust or LangSmith**; their evaluator signatures were not verified. Treat as a stub. |
| **JavaScript shim** (`js/mizan-ledger.mjs`): commitment, declare, verify opening. Node >= 18, `node:crypto` only. | Tested against the shared vectors (`node --test js/test.mjs`). It does not implement the file ledger. |

No CI workflow file is included in this repository.

## Local MCP server (optional)

An optional stdio [MCP](https://modelcontextprotocol.io) server lets an agent host (Cursor, Claude Desktop, VS Code or any MCP client) use the ledger without leaving the machine. It needs Python >= 3.10; the core install is unchanged.

```bash
pip install "mizan-ledger[mcp]"
mizan-ledger mcp              # same server as the mizan-ledger-mcp script
```

```json
{"mcpServers": {"mizan-ledger": {"command": "mizan-ledger-mcp",
  "env": {"MIZAN_LEDGER_DIR": ".mizan", "MIZAN_AGENT_ID": "my-agent"}}}}
```

Tools: `commit_confidence` (declare before the task), `resolve_outcome` (record the real outcome), `get_calibration` (read-only scores), `verify_ledger` (read-only offline check), `should_act` (pre-action gate; writes only with `commit=true`). Local files only: no network, no keys, no shell commands. The outcome you supply is not verified; prefer a source the agent does not control. `MIZAN_PRIVATE_DIR` (default `<MIZAN_LEDGER_DIR>/private`) holds the openings; keep it out of version control.

This is not the remote `qants-diwan` MCP server on the QANTS site (Mizan benchmark and directory); that is a different product.

## Tests

```bash
python3 -m pytest -q          # Python suite
node --test js/test.mjs       # JS shim vs shared vectors
```

## Seed agents

`seeds/` holds three demonstration agents operated by QANTS (ids start with `qants-`). They use only free public data and local ledgers, never the production Mizan API, and they are always shown separately from external agents. Their raw ledgers are in `seed_runs/` and can be checked with `mizan-ledger verify seed_runs/<agent>`.

## Security notes

Keep `private/` out of version control and backups you share. The salt is what stops guessing of low-entropy declarations.

## License

MIT, see `LICENSE`. Copyright (c) 2026 QANTS.
