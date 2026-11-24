# QANTS Mizan — agent calibration check

Python package `mizan-ledger`, published by QANTS. Not related to other projects named "mizan".

<!-- mcp-name: io.github.qants-labs/mizan-ledger -->

Declare how confident your agent is **before** a task, commit a salted hash of that declaration, then score honesty against the **real outcome** (your own tests, evals or checks). Local-first, hash-only, verifiable offline.

> Status: **v0.2.0 (prepared, not yet released), early alpha** by QANTS. On PyPI (not on npm). MIT licensed (see `LICENSE`). No network access, no telemetry, no dependencies in the core library.

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

## Add Mizan to CI in 5 minutes

`mizan-ledger gate` fails a pull request when your agent got more **confident and wrong** than on your main-branch
baseline. It reads your own results; nothing leaves your machine.

**Input**, either of:
- a ledger directory written by the pytest plugin (tests marked `@pytest.mark.confidence(p)`), or
- a JSONL file written by your own eval harness, one item per line:

```json
{"id": "q1", "confidence": 0.85, "correct": true}
{"id": "q2", "confidence": null, "correct": false}
```

`confidence` is a number from 0 to 1, or `null` for "I don't know". JSONL items are converted to a ledger internally,
so both inputs are scored the same way.

**Run it:**

```bash
mizan-ledger gate results.jsonl --baseline mizan-baseline.json     # or: mizan-ledger gate .mizan --baseline ...
```

**Metrics:**

| Metric | Can fail the run? |
|---|---|
| Brier score | yes |
| Overconfident-wrong share: wrong answers at confidence ≥ 0.9, as a share of answered items | yes |
| ECE (10 bins) | no, reported only |
| Abstain rate: `null` confidence, as a share of all items | no, reported only. With ledger input: "not recorded (ledger format)", because a ledger cannot hold a `null` confidence |

`null` items count only toward the abstain rate; they are left out of Brier, ECE and the overconfident-wrong share.
Every report states "answered N of M items", including when there is not enough data.

**Rules:**

| Situation | Result | Exit |
|---|---|---|
| Fewer than `--min-n` answered items (default 50) | "not enough data", no score shown | 0 |
| No baseline file yet | "inconclusive: baseline created" (commit the new file) | 0, or 2 with `--strict` |
| Brier or overconfident-wrong share worse than the baseline by more than its threshold, **even at the best end of the 95% bootstrap interval** | fail | 1 |
| Input does not verify or cannot be read | fail | 1 |
| Otherwise | pass | 0 |

A worse point estimate whose interval still reaches back within the threshold passes, with a note: small eval sets are
noisy.

**Thresholds** are QANTS starting values, not industry standards. Every report labels them so, and every one is a
flag: `--max-brier-delta 0.02`, `--max-ocw-delta 0.02`, `--overconfident-at 0.9`, `--min-n 50`.
`--write-baseline` refreshes the baseline on purpose.

**Where the baseline lives in CI**, one of:
- **Actions cache (default).**
  - A push to the default branch saves that run's baseline under `mizan-baseline-<agent-id>-<run id>`.
  - A pull request restores the newest one for the same `agent-id`.
  - If none exists yet, the run reports "inconclusive: baseline created" and passes.
  - Caches expire after a period without use, after which the next run starts a fresh baseline.
- **A committed file.** Set `baseline: .mizan/baseline.json` and commit that file to the default branch.
  - The action then uses it as is and never touches the cache.
  - Refresh it on purpose with `mizan-ledger gate <input> --baseline .mizan/baseline.json --write-baseline`.

**GitHub Actions:** use the composite action in this repository (`action.yml`).
- It runs pytest with the plugin, or reads your file when you set `jsonl:`.
- It runs the gate and writes a job summary.
- A ready workflow is in `examples/ci/`.
- No account, key, wallet or token is needed.

The gate measures calibration of stated confidence against your own outcomes. It does not show that the agent is good,
safe or secure.

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

Tools: `commit_confidence` (declare before the task), `resolve_outcome` (record the real outcome), `get_calibration` (read-only scores), `verify_ledger` (read-only offline check). Local files only: no network, no keys, no shell commands. The outcome you supply is not verified; prefer a source the agent does not control. `MIZAN_PRIVATE_DIR` (default `<MIZAN_LEDGER_DIR>/private`) holds the openings; keep it out of version control.

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
