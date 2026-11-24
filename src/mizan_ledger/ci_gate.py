"""CI calibration gate (Mizan Gate): compare this run's calibration with a committed baseline.

    mizan-ledger gate <ledger_dir | items.jsonl> --baseline mizan-baseline.json
    mizan-ledger gate <ledger_dir | items.jsonl> --baseline mizan-baseline.json --write-baseline

Input: a ledger directory, or a JSONL file with one item per line:
    {"id": "q1", "confidence": 0.85, "correct": true}
    {"id": "q2", "confidence": null, "correct": false}      # null = "I don't know" (abstained)
JSONL items with a confidence are converted to a temporary ledger (declare, then resolve) so both inputs share one
scoring path. Abstained items cannot be declared in a ledger (p must be in [0, 1]); they are counted for the abstain
rate only and excluded from every other metric.

Metrics
  can fail the run:  Brier score; overconfident-wrong share (wrong answers at confidence >= 0.9, as a share of
                     answered items)
  report only:       ECE (10 bins); abstain rate (null confidence, as a share of all items)

Rules (thresholds are QANTS starting values, not industry standards; all are configurable)
  * Fewer than --min-n answered items (default 50): the report says "not enough data", shows no score, exit 0.
  * No baseline yet: the baseline is created from this run, the report says "inconclusive: baseline created", exit 0.
    With --strict this case exits 2.
  * Fail (exit 1) only when, for Brier or for the overconfident-wrong share, even the best (lowest) end of the current
    percentile-bootstrap 95% interval is worse than the baseline value by more than that metric's threshold.
    A point estimate that is worse but whose interval still reaches back within the threshold passes, with a note.
  * A ledger that does not verify, or JSONL that cannot be read, exits 1.
  * Otherwise exit 0.

Pure standard library. No network. Reads only the public ledger files.
"""
from __future__ import annotations
import json, random, tempfile
from pathlib import Path
from typing import Optional

from . import metrics
from .core import Ledger, LedgerError, verify_ledger_dir

PASS, FAIL, STRICT_NO_BASELINE = 0, 1, 2
INCONCLUSIVE = STRICT_NO_BASELINE          # kept for callers of the first version of this module
BASELINE_SCHEMA = "mizan-ledger-baseline/2"
THRESHOLD_LABEL = "QANTS starting values, not industry standards"
ABSTAIN_NOT_RECORDED = "not recorded (ledger format)"   # a ledger cannot hold a null confidence
DEFAULTS = {"min_n": 50, "max_brier_delta": 0.02, "max_ocw_delta": 0.02, "overconfident_at": 0.9}
_REPS, _SEED = 2000, 20261116


# ---------------------------------------------------------------------------------------------------------------------
# inputs
# ---------------------------------------------------------------------------------------------------------------------
def read_jsonl_items(path) -> list:
    """Parse and validate the JSONL item file. Raises LedgerError with the line number on bad input."""
    items, seen = [], set()
    with open(path, "r", encoding="utf-8") as f:
        for i, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError as e:
                raise LedgerError(f"{path}: line {i} is not valid JSON ({e})")
            if not isinstance(obj, dict) or "id" not in obj or "confidence" not in obj or "correct" not in obj:
                raise LedgerError(f"{path}: line {i} needs id, confidence and correct")
            iid, c, ok = str(obj["id"]), obj["confidence"], obj["correct"]
            if iid in seen:
                raise LedgerError(f"{path}: line {i} repeats id {iid!r}")
            seen.add(iid)
            if not isinstance(ok, bool):
                raise LedgerError(f"{path}: line {i}: correct must be true or false")
            if c is not None and (isinstance(c, bool) or not isinstance(c, (int, float)) or not 0.0 <= float(c) <= 1.0):
                raise LedgerError(f"{path}: line {i}: confidence must be a number in [0, 1] or null")
            items.append({"id": iid, "confidence": None if c is None else float(c), "correct": ok})
    return items


def jsonl_to_ledger(items: list, ledger_dir, agent_id: str = "gate", version: str = "jsonl") -> int:
    """Declare and resolve every answered item in a ledger at ledger_dir. Returns the number of abstained items."""
    led = Ledger(ledger_dir, private_path=str(Path(ledger_dir) / "private"))
    abstained = 0
    for it in items:
        if it["confidence"] is None:
            abstained += 1
            continue
        led.declare(it["id"], it["confidence"], agent_id, version)
        led.resolve(it["id"], it["correct"], agent_id, version)
    return abstained


def _load(source, agent=None, version=None) -> dict:
    """Read a ledger dir or a JSONL file into {ok, errors, pairs, abstained, total, input}."""
    src = Path(source)
    if src.is_file():
        try:
            items = read_jsonl_items(src)
        except (LedgerError, OSError) as e:
            return {"ok": False, "errors": [str(e)], "input": "jsonl"}
        with tempfile.TemporaryDirectory() as tmp:
            abstained = jsonl_to_ledger(items, tmp)
            rep = verify_ledger_dir(tmp)
            pairs = Ledger(tmp, private_path=str(Path(tmp) / "private")).pairs()
        return {"ok": rep["ok"], "errors": rep["errors"], "pairs": pairs, "abstained": abstained,
                "total": len(items), "input": "jsonl"}
    if not src.is_dir():
        return {"ok": False, "errors": [f"not a ledger directory or JSONL file: {source}"], "input": "unknown"}
    rep = verify_ledger_dir(src)
    pairs = Ledger(src, private_path=str(src / ".noprivate")).pairs(agent, version)
    # a ledger cannot hold "I don't know" (null confidence): abstains are not recorded, never reported as 0
    return {"ok": rep["ok"], "errors": rep["errors"], "pairs": pairs, "abstained": None, "total": len(pairs),
            "input": "ledger"}


# ---------------------------------------------------------------------------------------------------------------------
# metrics
# ---------------------------------------------------------------------------------------------------------------------
def _boot(values: list) -> list:
    """Percentile bootstrap 95% interval of a mean (same method and seed as metrics.brier_interval)."""
    n = len(values)
    if n < 2:
        return [None, None]
    rng = random.Random(_SEED)
    means = sorted(sum(values[rng.randrange(n)] for _ in range(n)) / n for _ in range(_REPS))
    return [means[int(0.025 * _REPS)], means[int(0.975 * _REPS) - 1]]


def measure(source, agent=None, version=None, overconfident_at: float = DEFAULTS["overconfident_at"]) -> dict:
    d = _load(source, agent, version)
    if not d["ok"]:
        return {"ok": False, "errors": d["errors"], "n": 0, "input": d.get("input")}
    pairs = d["pairs"]
    if d["abstained"] is None:
        abstained = abstain_rate = ABSTAIN_NOT_RECORDED
    else:
        abstained, abstain_rate = d["abstained"], (d["abstained"] / d["total"]) if d["total"] else None
    out = {"ok": True, "errors": [], "input": d["input"], "n": len(pairs), "items": d["total"],
           "answered": f"answered {len(pairs)} of {d['total']} items", "abstained": abstained, "abstain_rate": abstain_rate}
    if pairs:
        bi = metrics.brier_interval(pairs, reps=_REPS, seed=_SEED)
        ocw = [1.0 if (p >= overconfident_at and not y) else 0.0 for p, y in pairs]
        out.update(brier=bi["brier"], brier_ci95=bi["ci95"], ocw_share=sum(ocw) / len(ocw), ocw_ci95=_boot(ocw),
                   ece=metrics.ece(pairs), ci_method=f"percentile bootstrap, {_REPS} reps, seed {_SEED}")
    return out


def _thresholds(min_n, max_brier_delta, max_ocw_delta, overconfident_at) -> dict:
    return {"min_n": min_n, "max_brier_delta": max_brier_delta, "max_ocw_delta": max_ocw_delta,
            "overconfident_at": overconfident_at, "label": THRESHOLD_LABEL}


def _scrub(m: dict) -> dict:
    """Below min_n the report must not show any score."""
    return {k: v for k, v in m.items() if k not in ("brier", "brier_ci95", "ocw_share", "ocw_ci95", "ece", "ci_method")}


def _baseline_doc(m: dict, agent, version) -> dict:
    return {"schema": BASELINE_SCHEMA, "agent_id": agent, "agent_version": version, "n": m["n"],
            "brier": m["brier"], "brier_ci95": m["brier_ci95"], "ocw_share": m["ocw_share"], "ocw_ci95": m["ocw_ci95"],
            "ece": m["ece"], "abstain_rate": m["abstain_rate"], "ci_method": m["ci_method"]}


# ---------------------------------------------------------------------------------------------------------------------
# gate
# ---------------------------------------------------------------------------------------------------------------------
def write_baseline(source, baseline_path, min_n=DEFAULTS["min_n"], agent=None, version=None,
                   overconfident_at=DEFAULTS["overconfident_at"]) -> tuple:
    m = measure(source, agent, version, overconfident_at)
    th = _thresholds(min_n, DEFAULTS["max_brier_delta"], DEFAULTS["max_ocw_delta"], overconfident_at)
    if not m["ok"]:
        return FAIL, {**m, "thresholds": th, "result": "fail", "reason": "input does not verify; no baseline written"}
    if m["n"] < min_n:
        return PASS, {**_scrub(m), "thresholds": th, "result": "not enough data",
                      "reason": f"{m['n']} answered items (< min_n {min_n}); no baseline written and no score shown"}
    Path(baseline_path).parent.mkdir(parents=True, exist_ok=True)
    Path(baseline_path).write_text(json.dumps(_baseline_doc(m, agent, version), indent=2, sort_keys=True) + "\n",
                                   encoding="utf-8")
    return PASS, {**m, "thresholds": th, "result": "baseline written", "reason": f"baseline written to {baseline_path}"}


def check(source, baseline_path, max_brier_delta=DEFAULTS["max_brier_delta"], min_n=DEFAULTS["min_n"], agent=None,
          version=None, max_ocw_delta=DEFAULTS["max_ocw_delta"], overconfident_at=DEFAULTS["overconfident_at"],
          strict: bool = False) -> tuple:
    """Returns (exit_code, report)."""
    if max_brier_delta < 0 or max_ocw_delta < 0:
        raise ValueError("thresholds must be >= 0")
    th = _thresholds(min_n, max_brier_delta, max_ocw_delta, overconfident_at)
    m = measure(source, agent, version, overconfident_at)
    if not m["ok"]:
        return FAIL, {**m, "thresholds": th, "result": "fail",
                      "reason": "input does not verify or cannot be read: " + "; ".join(m["errors"][:3])}
    if m["n"] < min_n:
        return PASS, {**_scrub(m), "thresholds": th, "result": "not enough data",
                      "reason": f"{m['n']} answered items (< min_n {min_n}); not enough data, no score shown"}
    bp = Path(baseline_path)
    if not bp.exists():
        bp.parent.mkdir(parents=True, exist_ok=True)
        bp.write_text(json.dumps(_baseline_doc(m, agent, version), indent=2, sort_keys=True) + "\n", encoding="utf-8")
        rep = {**m, "thresholds": th, "result": "inconclusive: baseline created",
               "reason": f"no baseline existed; created {bp} from this run (commit it so later runs compare against it)"}
        return (STRICT_NO_BASELINE if strict else PASS), rep
    base = json.loads(bp.read_text(encoding="utf-8"))
    rep = {**m, "thresholds": th, "baseline": {k: base.get(k) for k in ("n", "brier", "ocw_share", "ece", "abstain_rate")}}
    failures, notes = [], []
    for name, cur, ci, delta in (("Brier", m["brier"], m["brier_ci95"], max_brier_delta),
                                 ("overconfident-wrong share", m["ocw_share"], m["ocw_ci95"], max_ocw_delta)):
        b = base.get("brier" if name == "Brier" else "ocw_share")
        if b is None:
            notes.append(f"{name}: not in this baseline; not checked")
            continue
        lo = ci[0]
        if lo is not None and lo - b > delta:
            failures.append(f"{name} {cur:.4f} vs baseline {b:.4f}: even the low end of the 95% interval ({lo:.4f}) "
                            f"is worse by more than {delta}")
        elif cur - b > delta:
            notes.append(f"{name}: point estimate worse by {cur - b:.4f}, but the 95% interval [{lo:.4f}, {ci[1]:.4f}] "
                         f"still reaches back within {delta}; not counted as a regression")
    rep["brier_delta"] = m["brier"] - base["brier"] if base.get("brier") is not None else None
    rep["ocw_delta"] = m["ocw_share"] - base["ocw_share"] if base.get("ocw_share") is not None else None
    if failures:
        return FAIL, {**rep, "result": "fail", "reason": "; ".join(failures + notes)}
    return PASS, {**rep, "result": "pass", "reason": "no regression beyond the thresholds" + ("; " + "; ".join(notes) if notes else "")}


def markdown(code: int, rep: dict) -> str:
    """A short Markdown job summary (for $GITHUB_STEP_SUMMARY). Shows no score when there is not enough data."""
    result = rep.get("result", "")
    label = {"pass": "PASS", "fail": "FAIL", "not enough data": "NOT ENOUGH DATA", "baseline written": "BASELINE WRITTEN",
             "inconclusive: baseline created": "INCONCLUSIVE: BASELINE CREATED"}.get(result, result.upper())
    th = rep.get("thresholds") or {}
    def f(x, d=4):
        return "n/a" if x is None else f"{x:.{d}f}"
    def ci(v):
        return f"[{f(v[0])}, {f(v[1])}]" if v else "n/a"
    def ar(x):
        return x if isinstance(x, str) else f(x)
    lines = [f"### Mizan calibration gate: {label}", ""]
    if "brier" in rep:
        base = rep.get("baseline") or {}
        lines += ["| | current | 95% interval | baseline | fails the run? |", "|---|---|---|---|---|",
                  f"| Brier | {f(rep.get('brier'))} | {ci(rep.get('brier_ci95'))} | {f(base.get('brier'))} | "
                  f"yes, if worse by > {th.get('max_brier_delta')} |",
                  f"| Overconfident-wrong share (wrong at confidence >= {th.get('overconfident_at')}) | "
                  f"{f(rep.get('ocw_share'))} | {ci(rep.get('ocw_ci95'))} | {f(base.get('ocw_share'))} | "
                  f"yes, if worse by > {th.get('max_ocw_delta')} |",
                  f"| ECE (10 bins) | {f(rep.get('ece'))} | | {f(base.get('ece'))} | no, report only |",
                  f"| Abstain rate | {ar(rep.get('abstain_rate'))} | | {ar(base.get('abstain_rate'))} | no, report only |", ""]
    answered = rep.get("answered") or f"answered {rep.get('n', 0)} of {rep.get('items', rep.get('n', 0))} items"
    lines += [f"{answered[0].upper()}{answered[1:]} (abstained: {ar(rep.get('abstained'))}); minimum {th.get('min_n')} answered.",
              "", f"{rep.get('reason', '')}", "",
              f"_Thresholds: {THRESHOLD_LABEL}. Calibration of stated confidence against your own outcomes only; it does "
              "not show that the agent is good, safe or secure._"]
    return "\n".join(lines) + "\n"
