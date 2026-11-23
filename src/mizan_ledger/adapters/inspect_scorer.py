"""Inspect AI scorer that records stated confidence in a mizan-ledger and scores calibration (optional extra).

    pip install "mizan-ledger[inspect]"
    inspect eval my_task.py --scorer mizan_ledger/mizan_calibration      # by name, via the inspect_ai entry point

    from mizan_ledger.adapters.inspect_scorer import mizan_calibration
    Task(dataset=..., solver=..., scorer=mizan_calibration(inner=match(), ledger_dir=".mizan", agent_id="my-model"))

For each sample: the model's stated confidence p is read from `state.metadata[confidence_key]`, or else from its output
text ("Confidence: 0.8" / "confidence=0.8"). The inner scorer (default `match()`) decides pass/fail. The scorer then
declares p and resolves it with that outcome in the ledger, and returns CORRECT/INCORRECT with p and the squared error.
Metrics: accuracy, stderr and `brier` (mean squared error of stated confidence).

Honest limits: the declaration is written at scoring time, after the model answered but before this scorer decided the
outcome; the ledger proves that order only. A sample with no stated confidence is returned unscored by the ledger
(metadata says so) rather than given an invented p. The outcome comes from the inner scorer, not from this library.
This module imports inspect_ai and is NOT imported by the core package.
"""
from __future__ import annotations
import datetime as _dt
import re
from typing import Optional, Union

from inspect_ai.scorer import (CORRECT, INCORRECT, Score, Scorer, Target, accuracy, includes, match, metric, scorer,
                               stderr, value_to_float)
from inspect_ai.solver import TaskState

from ..core import Ledger, LedgerError

_CONF = re.compile(r"confidence\s*[:=]\s*(0(?:\.\d+)?|1(?:\.0+)?|\.\d+)", re.IGNORECASE)
_INNER = {"match": match, "includes": includes}


def stated_confidence(state: TaskState, confidence_key: str = "confidence") -> Optional[float]:
    v = (state.metadata or {}).get(confidence_key)
    if v is None:
        m = _CONF.search(state.output.completion or "") if state.output else None
        v = m.group(1) if m else None
    try:
        p = float(v) if v is not None else None
    except (TypeError, ValueError):
        return None
    return p if p is not None and 0.0 <= p <= 1.0 else None


@metric
def brier():
    """Mean squared error of the stated confidence over samples that stated one (lower is better)."""
    def compute(scores) -> float:
        vals = []
        for s in scores:
            sc = getattr(s, "score", s)
            se = (sc.metadata or {}).get("squared_error")
            if se is not None:
                vals.append(float(se))
        return sum(vals) / len(vals) if vals else float("nan")
    return compute


@scorer(metrics=[accuracy(), stderr(), brier()])
def mizan_calibration(inner: Union[Scorer, str] = "match", confidence_key: str = "confidence",
                      ledger_dir: str = ".mizan", private_dir: Optional[str] = None, agent_id: str = "inspect-agent",
                      agent_version: str = "unspecified", run_id: Optional[str] = None) -> Scorer:
    base = _INNER[inner]() if isinstance(inner, str) else inner
    run = run_id or _dt.datetime.now(_dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    ledger = Ledger(ledger_dir, private_path=private_dir)

    async def score(state: TaskState, target: Target) -> Score:
        s = await base(state, target)
        ok = value_to_float()(s.value) >= 1.0
        p = stated_confidence(state, confidence_key)
        meta = dict(s.metadata or {})
        if p is None:
            meta["mizan"] = "no stated confidence: not recorded in the ledger"
            return Score(value=CORRECT if ok else INCORRECT, answer=s.answer, explanation=s.explanation, metadata=meta)
        task_id = f"{state.sample_id}@{run}"
        try:
            rec = ledger.declare(task_id, p, agent_id, agent_version)
            ledger.resolve(task_id, ok, agent_id, agent_version)
        except LedgerError as e:
            meta["mizan"] = f"not recorded: {e}"
            return Score(value=CORRECT if ok else INCORRECT, answer=s.answer, explanation=s.explanation, metadata=meta)
        se = (p - (1 if ok else 0)) ** 2
        meta.update(confidence=p, squared_error=se, mizan_commitment=rec["commitment"])
        return Score(value=CORRECT if ok else INCORRECT, answer=s.answer,
                     explanation=f"stated confidence {p:.3f}; {'correct' if ok else 'incorrect'}; squared error {se:.4f}",
                     metadata=meta)

    return score
