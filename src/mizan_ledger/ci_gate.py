"""CI calibration gate: compare a ledger's Brier score with a committed baseline.

    mizan-ledger gate <ledger_dir> --baseline baseline.json --max-brier-delta 0.02 --min-n 50
    mizan-ledger gate <ledger_dir> --baseline baseline.json --write-baseline

Exit codes
  0  pass: calibration did not get worse beyond the allowed delta
  1  fail: calibration got worse beyond the delta AND beyond the sampling interval, or the ledger does not verify
  2  inconclusive: too few resolved records (or no baseline yet). This is a warning, never a pass or a fail.

The fail rule is deliberately conservative. With Brier B (baseline) and the current percentile-bootstrap 95% interval
[lo, hi] from brier_interval(), the gate fails only when lo - B > max_delta: even the optimistic end of the current
interval is worse than the baseline by more than the delta. A point estimate that is worse but whose interval still
reaches back within the delta is reported as a pass with a note, because small samples are noisy.

Pure standard library. No network. Reads only the public ledger files.
"""
from __future__ import annotations
import json
from pathlib import Path
from typing import Optional

from . import metrics
from .core import verify_ledger_dir

PASS, FAIL, INCONCLUSIVE = 0, 1, 2
BASELINE_SCHEMA = "mizan-ledger-baseline/1"


def _pairs(ledger_dir, agent: Optional[str], version: Optional[str]) -> list:
    from .core import Ledger
    return Ledger(ledger_dir, private_path=str(Path(ledger_dir) / ".noprivate")).pairs(agent, version)


def measure(ledger_dir, agent: Optional[str] = None, version: Optional[str] = None) -> dict:
    """Verify the ledger and compute Brier (with interval), ECE and n for the selected agent/version."""
    if not Path(ledger_dir).is_dir():
        return {"ok": False, "errors": [f"not a directory: {ledger_dir}"], "n": 0}
    rep = verify_ledger_dir(ledger_dir)
    pairs = _pairs(ledger_dir, agent, version)
    out = {"ok": rep["ok"], "errors": rep["errors"], "n": len(pairs), "agent_id": agent, "agent_version": version,
           "chain_heads": rep.get("chain_heads")}
    if pairs:
        bi = metrics.brier_interval(pairs)
        out.update(brier=bi["brier"], brier_ci95=bi["ci95"], brier_ci_method=bi["method"], ece=metrics.ece(pairs))
    return out


def write_baseline(ledger_dir, baseline_path, min_n: int = 50, agent=None, version=None) -> tuple:
    m = measure(ledger_dir, agent, version)
    if not m["ok"]:
        return FAIL, {**m, "result": "fail", "reason": "ledger does not verify; refusing to write a baseline from it"}
    if m["n"] < min_n:
        return INCONCLUSIVE, {**m, "result": "inconclusive",
                              "reason": f"only {m['n']} resolved records (< min_n {min_n}); a baseline this small would be noise"}
    base = {"schema": BASELINE_SCHEMA, "agent_id": agent, "agent_version": version, "n": m["n"], "brier": m["brier"],
            "brier_ci95": m["brier_ci95"], "brier_ci_method": m["brier_ci_method"], "ece": m["ece"],
            "chain_heads": m["chain_heads"]}
    Path(baseline_path).write_text(json.dumps(base, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return PASS, {**m, "result": "baseline written", "baseline_path": str(baseline_path)}


def check(ledger_dir, baseline_path, max_brier_delta: float = 0.02, min_n: int = 50, agent=None, version=None) -> tuple:
    """Returns (exit_code, report)."""
    if max_brier_delta < 0:
        raise ValueError("max_brier_delta must be >= 0")
    m = measure(ledger_dir, agent, version)
    if not m["ok"]:
        return FAIL, {**m, "result": "fail", "reason": "ledger does not verify (hash chain, openings or scores): " +
                      "; ".join(m["errors"][:3])}
    bp = Path(baseline_path)
    if not bp.exists():
        return INCONCLUSIVE, {**m, "result": "inconclusive",
                              "reason": f"no baseline at {bp}; create one with --write-baseline once n >= {min_n}"}
    base = json.loads(bp.read_text(encoding="utf-8"))
    rep = {**m, "baseline": {k: base.get(k) for k in ("n", "brier", "brier_ci95", "ece")}, "max_brier_delta": max_brier_delta,
           "min_n": min_n}
    if m["n"] < min_n:
        return INCONCLUSIVE, {**rep, "result": "inconclusive",
                              "reason": f"only {m['n']} resolved records (< min_n {min_n}); not enough to pass or fail"}
    lo = m["brier_ci95"][0]
    delta = m["brier"] - base["brier"]
    rep["brier_delta"] = delta
    if lo is not None and lo - base["brier"] > max_brier_delta:
        return FAIL, {**rep, "result": "fail",
                      "reason": f"Brier {m['brier']:.4f} vs baseline {base['brier']:.4f}: even the low end of the 95% interval "
                                f"({lo:.4f}) is worse than the baseline by more than {max_brier_delta}"}
    note = ""
    if delta > max_brier_delta:
        note = (f" Point estimate is worse by {delta:.4f}, but the 95% interval [{lo:.4f}, {m['brier_ci95'][1]:.4f}] "
                f"still reaches back within the allowed delta, so this is not counted as a regression.")
    return PASS, {**rep, "result": "pass", "reason": f"Brier {m['brier']:.4f} vs baseline {base['brier']:.4f} "
                  f"(delta {delta:+.4f}, allowed {max_brier_delta}).{note}"}


def markdown(code: int, rep: dict) -> str:
    """A short Markdown job summary (for $GITHUB_STEP_SUMMARY)."""
    label = {PASS: "PASS", FAIL: "FAIL", INCONCLUSIVE: "INCONCLUSIVE (warning)"}[code]
    def f(x, d=4):
        return "n/a" if x is None else f"{x:.{d}f}"
    ci = rep.get("brier_ci95") or [None, None]
    base = rep.get("baseline") or {}
    lines = [f"### Mizan calibration gate: {label}", "",
             "| | current | baseline |", "|---|---|---|",
             f"| n (resolved) | {rep.get('n', 0)} | {base.get('n', 'n/a')} |",
             f"| Brier | {f(rep.get('brier'))} | {f(base.get('brier'))} |",
             f"| Brier 95% interval | [{f(ci[0])}, {f(ci[1])}] | |",
             f"| ECE | {f(rep.get('ece'))} | {f(base.get('ece'))} |", "",
             f"{rep.get('reason', '')}", "",
             "_Calibration of stated confidence against test outcomes only. It does not show that the agent is good, "
             "safe or secure. Small n gives wide intervals; ECE is biased upward for small n._"]
    return "\n".join(lines) + "\n"
