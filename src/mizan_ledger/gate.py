"""Pre-action gate: decide whether an agent should act, from its stated confidence and its own track record.

    from mizan_ledger.gate import should_act
    d = should_act(p=0.82, risk_threshold=0.95, ledger=Ledger(".mizan"), agent_id="my-agent")
    if not d.act:
        ...  # abstain: ask a human, retry, or stop. d.reason says why.

Rule: act only if p >= risk_threshold; otherwise ABSTAIN with a reason.

Adjustment (only with a track record): if a ledger is passed and it holds >= MIN_N_FOR_ADJUSTMENT resolved records for
the agent, p is replaced by an adjusted value from the agent's own reliability table: the observed success frequency
in p's bin, shrunk toward p in proportion to how few records the bin has,
    p_adj = (n_bin * freq_bin + PRIOR_WEIGHT * p) / (n_bin + PRIOR_WEIGHT).
Both the raw and the adjusted p are reported, and the decision uses the adjusted one. With fewer records, or an empty
bin, raw p is used and the reason says so explicitly.

Optional commit: with commit=True and a task_id, the stated (raw) p is declared in the ledger BEFORE the action, so the
task is scored later whether the agent acted or abstained (resolve it with the outcome you observe). The ledger stores
only the stated p; the ACT/ABSTAIN decision itself is returned to the caller, not written to the ledger.

This gate uses stated confidence and past calibration only. It cannot verify the agent's reasoning, and a well-calibrated
agent can still be wrong. Pure standard library, no network.
"""
from __future__ import annotations
from dataclasses import dataclass, asdict, field
from typing import Optional

from . import metrics
from .core import Ledger, LedgerError, fmt_p

MIN_N_FOR_ADJUSTMENT = 50
PRIOR_WEIGHT = 10
BINS = 10

ACT, ABSTAIN = "ACT", "ABSTAIN"


@dataclass
class Decision:
    decision: str
    act: bool
    p_raw: float
    p_adjusted: Optional[float]
    p_used: float
    risk_threshold: float
    adjusted: bool
    n_resolved: int
    reason: str
    bin: Optional[dict] = None
    receipt: Optional[dict] = field(default=None)

    def to_dict(self) -> dict:
        return asdict(self)


def _adjust(p: float, pairs: list) -> tuple:
    """(p_adjusted or None, bin_info, note)."""
    i = min(BINS - 1, int(p * BINS))
    rows = [(q, y) for q, y in pairs if min(BINS - 1, int(q * BINS)) == i]
    info = {"bin": f"{i / BINS:.1f}-{(i + 1) / BINS:.1f}", "n_bin": len(rows)}
    if not rows:
        return None, info, f"no past records in p's bin {info['bin']}"
    freq = sum(y for _, y in rows) / len(rows)
    info["freq_bin"] = round(freq, 4)
    adj = (len(rows) * freq + PRIOR_WEIGHT * p) / (len(rows) + PRIOR_WEIGHT)
    return adj, info, f"adjusted from the agent's record: bin {info['bin']} has {len(rows)} resolved, success rate {freq:.3f}"


def should_act(p: float, risk_threshold: float, ledger: Optional[Ledger] = None, agent_id: Optional[str] = None,
               version: Optional[str] = None, task_id: Optional[str] = None, commit: bool = False) -> Decision:
    p = float(p); risk_threshold = float(risk_threshold)
    fmt_p(p); fmt_p(risk_threshold)  # validate both are in [0, 1]
    pairs = ledger.pairs(agent_id, version) if ledger is not None else []
    n = len(pairs)
    p_adj, info, why = None, None, ""
    if ledger is None:
        why = "no ledger passed; using the stated p as is"
    elif n < MIN_N_FOR_ADJUSTMENT:
        why = (f"only {n} resolved records for this agent (< {MIN_N_FOR_ADJUSTMENT}); using the stated p as is, "
               f"without adjustment")
    else:
        p_adj, info, why = _adjust(p, pairs)
        if p_adj is None:
            why += "; using the stated p as is"
    used = p_adj if p_adj is not None else p
    act = used >= risk_threshold
    reason = (f"{'p' if p_adj is None else 'adjusted p'} {used:.3f} {'>=' if act else '<'} risk threshold "
              f"{risk_threshold:.3f} ({why})")
    receipt = None
    if commit:
        if ledger is None or not task_id or not agent_id:
            raise LedgerError("commit=True needs a ledger, a task_id and an agent_id")
        receipt = ledger.declare(task_id, p, agent_id, version or "unspecified")
    return Decision(decision=ACT if act else ABSTAIN, act=act, p_raw=p, p_adjusted=p_adj, p_used=used,
                    risk_threshold=risk_threshold, adjusted=p_adj is not None, n_resolved=n, reason=reason, bin=info,
                    receipt=receipt)
