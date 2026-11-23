"""promptfoo Python assertion that records stated confidence + pass/fail in a mizan-ledger.

promptfooconfig.yaml:

    tests:
      - vars: { question: "Capital of France?", expected: "Paris" }
        assert:
          - type: python
            value: file://mizan_assert.py
            config: { ledger_dir: .mizan, agent_id: my-model, expected_var: expected }

The model must state its confidence in its output ("Confidence: 0.8"), or the test must set a `confidence` var.
Pass/fail = the expected text appears in the output (case-insensitive). Returns {pass, score, reason}, where score is
1 - squared error of the stated confidence (1.0 = perfectly confident and right). A test with no stated confidence
fails with a clear reason rather than an invented p.

Honest limits: the declaration is written when this assertion runs, after the model answered; the ledger proves the
order of declaration and resolution only. Needs only mizan-ledger (no promptfoo import); promptfoo calls get_assert.
"""
import re
from mizan_ledger import Ledger, LedgerError

_CONF = re.compile(r"confidence\s*[:=]\s*(0(?:\.\d+)?|1(?:\.0+)?|\.\d+)", re.IGNORECASE)


def _confidence(output, variables, key):
    v = variables.get(key)
    if v is None:
        m = _CONF.search(output or "")
        v = m.group(1) if m else None
    try:
        p = float(v) if v is not None else None
    except (TypeError, ValueError):
        return None
    return p if p is not None and 0.0 <= p <= 1.0 else None


def get_assert(output, context):
    cfg = (context.get("config") or {}) if isinstance(context, dict) else {}
    variables = (context.get("vars") or {}) if isinstance(context, dict) else {}
    expected = variables.get(cfg.get("expected_var", "expected"))
    if expected is None:
        return {"pass": False, "score": 0.0, "reason": "no expected value in vars; nothing to check against"}
    ok = str(expected).lower() in str(output or "").lower()
    p = _confidence(output, variables, cfg.get("confidence_var", "confidence"))
    if p is None:
        return {"pass": False, "score": 0.0, "reason": "no stated confidence (output 'Confidence: <p>' or a confidence var)"}
    led = Ledger(cfg.get("ledger_dir", ".mizan"), private_path=cfg.get("private_dir"))
    agent, version = cfg.get("agent_id", "promptfoo-agent"), cfg.get("agent_version", "unspecified")
    task_id = f"{cfg.get('run_id', 'promptfoo')}:{context.get('prompt', '')[:60]}:{expected}:{variables.get('question', '')}"
    try:
        led.declare(task_id, p, agent, version)
        led.resolve(task_id, ok, agent, version)
    except LedgerError as e:
        return {"pass": ok, "score": 1.0 - (p - (1 if ok else 0)) ** 2, "reason": f"checked, but not recorded: {e}"}
    se = (p - (1 if ok else 0)) ** 2
    return {"pass": ok, "score": 1.0 - se,
            "reason": f"{'contains' if ok else 'missing'} expected text; stated confidence {p:.2f}; squared error {se:.4f}"}
