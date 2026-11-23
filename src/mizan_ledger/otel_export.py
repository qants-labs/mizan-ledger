"""Export resolved ledger records shaped like the OpenTelemetry GenAI event `gen_ai.evaluation.result`.

    mizan-ledger export-otel <ledger_dir> [--agent ID] [--version V] [--out FILE]

One JSON object per line (JSON Lines), one per resolved declaration. Output goes to stdout or a file only: no network,
no OpenTelemetry dependency. Feed the lines to your own collector or log pipeline.

Mapping (per record):
  gen_ai.evaluation.name         "mizan.calibration"
  gen_ai.evaluation.score.value  squared error of the stated confidence, (p - outcome)^2; lower is better
  gen_ai.evaluation.score.label  "correct" / "incorrect" (the supplied outcome)
  gen_ai.evaluation.explanation  "stated confidence p; outcome; squared error"
  mizan.commitment               correlation id: the record's commitment hash (also the hash in receipts.jsonl)
  mizan.task_id, gen_ai.agent.name, mizan.agent_version, mizan.p_stated, mizan.record_hash

The OpenTelemetry GenAI semantic conventions, including this event, are still in "Development" status and may change;
the attribute names above follow them as published at the time of writing and the mizan.* names are our own.
Scores measure calibration of stated confidence against supplied outcomes only.
"""
from __future__ import annotations
import json
from pathlib import Path
from typing import Iterator, Optional

from .core import _read_jsonl, verify_ledger_dir

EVENT_NAME = "gen_ai.evaluation.result"


def events(ledger_dir, agent: Optional[str] = None, version: Optional[str] = None) -> Iterator[dict]:
    for r in _read_jsonl(Path(ledger_dir) / "resolutions.jsonl"):
        if (agent is not None and r.get("agent_id") != agent) or (version is not None and r.get("agent_version") != version):
            continue
        p = float(r["opening"]["p"]); y = int(r["outcome"]); se = r["score"]["squared_error"]
        yield {
            "name": EVENT_NAME,
            "timestamp": r["resolved_at"],
            "attributes": {
                "gen_ai.evaluation.name": "mizan.calibration",
                "gen_ai.evaluation.score.value": se,
                "gen_ai.evaluation.score.label": "correct" if y else "incorrect",
                "gen_ai.evaluation.explanation": f"stated confidence {p:.3f}; outcome {'success' if y else 'failure'}; "
                                                 f"squared error {se:.4f}",
                "gen_ai.agent.name": r["agent_id"],
                "mizan.agent_version": r["agent_version"],
                "mizan.task_id": r["opening"]["task_id"],
                "mizan.p_stated": p,
                "mizan.commitment": r["commitment"],
                "mizan.record_hash": r["record_hash"],
            },
        }


def export(ledger_dir, out=None, agent=None, version=None) -> dict:
    """Write JSON Lines to `out` (a path) or return them. Refuses a ledger that does not verify."""
    rep = verify_ledger_dir(ledger_dir)
    if not rep["ok"]:
        return {"ok": False, "errors": rep["errors"], "lines": []}
    lines = [json.dumps(e, sort_keys=True, ensure_ascii=False) for e in events(ledger_dir, agent, version)]
    if out:
        Path(out).write_text("".join(l + "\n" for l in lines), encoding="utf-8")
    return {"ok": True, "errors": [], "lines": lines}
