# AGENTS.md

Instructions for coding agents working in this repository (convention: https://agents.md/).

## What this repo is

mizan-ledger is a local calibration ledger for AI agents. You declare a confidence **before** a task, the library commits a salted SHA-256 hash of that declaration, and after the outcome it scores the ledger with Brier, ECE and log loss (plus accuracy and coverage).

- Package: https://pypi.org/project/mizan-ledger/ (v0.1.5, early alpha, Python >= 3.9, MIT)
- Docs: https://qants-site.qants.workers.dev/library
- The core library (`src/mizan_ledger/`) has no required dependencies, no network access, and no telemetry. Do not add network calls, telemetry, or required dependencies to it.
- `seeds/` holds the three QANTS-operated demonstration agents. They are not part of the package; `seed_apifetch.py` and `seed_countrypairs.py` call free public APIs by design. Their ledgers are in `seed_runs/`.

## Honesty limits (do not weaken these)

- This library scores stated confidence against outcomes supplied by the user or by the pytest plugin. It does **not** verify that a claimed success is true.
- Outside the pytest path, prefer an outcome source the agent does not control. The pytest plugin uses the test result (pass or fail).
- Timestamps come from the local clock. They prove order only if the chain head is published somewhere independent.
- Small samples give wide intervals. ECE is biased upward for small n. Scores measure calibration, not whether an agent is good, safe or secure.
- Never invent published figures. Seed agents are QANTS-operated rule-based programs, not language-model agents, and are never counted as external. Current published seed totals and the external-agent KPI live on the site, not in this file (they change).

## How to try it

```bash
pip install mizan-ledger
# or from a checkout: pip install -e .[dev]
```

```python
# test_demo.py
import pytest

@pytest.mark.confidence(0.9)
def test_demo():
    assert True
```

```bash
pytest test_demo.py --mizan-ledger-dir .mizan --mizan-agent-id my-agent
```

Keep `.mizan/private/` and any `private/` openings directory out of version control.

The Python API (`Ledger.declare`, `Ledger.resolve`, `Ledger.summary`, `mizan-ledger verify`) is documented in README.md. Shared test vectors live in `vectors/commitments.json`. Seed ledger files, when present, live in `seed_runs/` and are not shipped on PyPI.

## Tests

```bash
python3 -m pytest -q          # Python suite
node --test js/test.mjs       # JS shim vs shared vectors (Node >= 18)
mizan-ledger verify seed_runs/qants-seed-codetask   # offline check of a seed ledger
```

Run both suites before proposing a change. Never skip or weaken a failing test to make it pass.

## Working in this repo

- Target Python 3.9 or later. Keep the core library dependency-free.
- Do not reformat unrelated files. Match the existing style.
- The only workflow is `.github/workflows/publish-mcp.yml` (manual MCP Registry publish). Do not add `.github/` workflows unless the maintainer asks for one.
- Work on a branch and open a pull request; do not push to `main` directly.
- A declaration cannot be replaced or re-resolved; deleting or editing a record must break the hash chain so `verify` fails. Do not add APIs that rewrite history.
- Coverage must keep being reported (declared, resolved, pending).
- If you add an optional MCP extra, keep it optional: the core install must stay offline and dependency-free. A local stdio MCP server is a wrapper around `Ledger`, not a remote service. The registry entry `io.github.qants-labs/mizan` (published manually by `.github/workflows/publish-mcp.yml`) points at the site's remote `/mcp` endpoint by maintainer decision; do not add other remote listings without approval.
- Do not name a private individual as author in new public metadata. The publisher is QANTS.
- Public times, when needed, are GMT+2. Do not name a country.
