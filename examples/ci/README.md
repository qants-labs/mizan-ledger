# Mizan Gate in CI

1. Produce results, either way:
   - pytest: mark tests with `@pytest.mark.confidence(p)` (the stated probability the test passes); the action runs
     pytest with the plugin.
   - your own eval harness: write `results.jsonl`, one line per item:
     `{"id": "q1", "confidence": 0.85, "correct": true}`, with `confidence: null` for "I don't know".
2. Copy `mizan-gate.yml` to `.github/workflows/` in your repository.
3. Baseline:
   - **Default:** a push to the default branch saves the run's baseline in the Actions cache. Pull requests restore
     the newest one for the same `agent-id`. Before the first push, a pull request reports "inconclusive: baseline
     created" and passes; with `strict: "true"` it fails (exit 2) instead.
   - **Alternative:** commit the baseline file, e.g. `baseline: .mizan/baseline.json`. The action then uses it as is
     and never uses the cache.

Exit codes of `mizan-ledger gate`:

| Exit | Meaning |
|---|---|
| 0 | pass, not enough data (no score shown), or baseline created |
| 1 | Brier or overconfident-wrong share worse beyond its threshold even at the best end of the 95% interval, or unreadable or unverified input |
| 2 | no baseline yet, with `--strict` only |

Thresholds are QANTS starting values, not industry standards. Keep `.mizan/private/` out of version control.
