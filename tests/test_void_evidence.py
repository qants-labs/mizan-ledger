"""v0.2: void outcome and sealed resolver evidence."""
import json

import pytest

from mizan_ledger import Ledger
from mizan_ledger.core import LedgerError, verify_ledger_dir


def _ledger(tmp_path):
    return Ledger(tmp_path / "pub", private_path=tmp_path / "priv")


def _rewrite_last(path, mutate):
    lines = path.read_text().splitlines()
    rec = json.loads(lines[-1]); mutate(rec)
    lines[-1] = json.dumps(rec, sort_keys=True, separators=(",", ":"))
    path.write_text("\n".join(lines) + "\n")


def test_void_is_final_unscored_and_counts_against_coverage(tmp_path):
    led = _ledger(tmp_path)
    led.declare("q1", 0.8, agent_id="a"); led.declare("q2", 0.3, agent_id="a"); led.declare("q3", 0.6, agent_id="a")
    led.resolve("q1", True)
    rec = led.void("q2", "LATE_READ_VOID: read after grace")
    assert rec["outcome"] == "void" and "score" not in rec
    assert rec["opening"]["task_id"] == "q2"          # opening is still revealed
    s = led.summary("a")
    assert (s["n"], s["declared"], s["resolved"], s["voided"], s["pending"]) == (1, 3, 1, 1, 1)
    assert s["coverage"] == pytest.approx(1 / 3)
    with pytest.raises(LedgerError):
        led.resolve("q2", False)                       # a void cannot be turned into an outcome
    with pytest.raises(LedgerError):
        led.void("q1", "too late")                     # an outcome cannot be voided afterwards
    rep = verify_ledger_dir(tmp_path / "pub")
    assert rep["ok"], rep["errors"]
    assert (rep["summary"]["resolved"], rep["summary"]["voided"], rep["summary"]["pending"]) == (1, 1, 1)


def test_void_needs_reason(tmp_path):
    led = _ledger(tmp_path); led.declare("q", 0.5, agent_id="a")
    for bad in ("", "   ", None):
        with pytest.raises(LedgerError):
            led.void("q", bad)


def test_evidence_is_sealed(tmp_path):
    led = _ledger(tmp_path); led.declare("ecb", 0.7, agent_id="a")
    ev = {"source": "https://example.org/ecb", "value_read": "1.0992", "threshold": "1.1050",
          "read_at": "2026-11-20T09:05:00+02:00", "raw_sha256": "ab" * 32}
    rec = led.resolve("ecb", False, evidence=ev)
    assert rec["evidence"] == ev
    assert verify_ledger_dir(tmp_path / "pub")["ok"]
    _rewrite_last(tmp_path / "pub" / "resolutions.jsonl", lambda r: r["evidence"].update(value_read="1.2000"))
    rep = verify_ledger_dir(tmp_path / "pub")
    assert not rep["ok"] and any("hash mismatch" in e for e in rep["errors"])


def test_void_with_evidence_and_tampered_reason(tmp_path):
    led = _ledger(tmp_path); led.declare("cg", 0.4, agent_id="a")
    led.void("cg", "MISSED_FIRST_READ_WINDOW", evidence={"window": "[+30m,+90m]", "checked_at": "2026-11-22T16:35:00+02:00"})
    assert verify_ledger_dir(tmp_path / "pub")["ok"]
    _rewrite_last(tmp_path / "pub" / "resolutions.jsonl", lambda r: r.update(void_reason="other"))
    assert not verify_ledger_dir(tmp_path / "pub")["ok"]


@pytest.mark.parametrize("bad", [{}, [], "x", {"a": {"nested": 1}}, {"a": float("nan")}, {1: "x"}])
def test_bad_evidence_rejected(tmp_path, bad):
    led = _ledger(tmp_path); led.declare("q", 0.5, agent_id="a")
    with pytest.raises(LedgerError):
        led.resolve("q", True, evidence=bad)


def test_void_cannot_carry_score(tmp_path):
    led = _ledger(tmp_path); led.declare("q", 0.5, agent_id="a"); led.void("q", "x")
    p = tmp_path / "pub" / "resolutions.jsonl"
    def add_score(r):
        r["score"] = {"squared_error": 0.0, "log_loss": 0.0}
        from mizan_ledger.core import _chain_hash
        r["record_hash"] = _chain_hash(r, "record_hash")   # forger recomputes the hash
    _rewrite_last(p, add_score)
    rep = verify_ledger_dir(tmp_path / "pub")
    assert not rep["ok"] and any("must not carry a score" in e for e in rep["errors"])


def test_v01_ledgers_still_verify(tmp_path):
    led = _ledger(tmp_path); led.declare("q", 0.9, agent_id="a"); led.resolve("q", True)
    rec = json.loads((tmp_path / "pub" / "resolutions.jsonl").read_text())
    assert "evidence" not in rec and "void_reason" not in rec
    assert verify_ledger_dir(tmp_path / "pub")["ok"]
