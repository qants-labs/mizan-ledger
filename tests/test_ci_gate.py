import json
import pytest
from mizan_ledger import Ledger
from mizan_ledger import ci_gate
from mizan_ledger.cli import main


def _clock():
    n = [0]
    def c():
        n[0] += 1
        return f"2026-11-23T{n[0] // 3600:02d}:{n[0] // 60 % 60:02d}:{n[0] % 60:02d}.000Z"
    return c


def make_ledger(path, p, hits, n, agent="a"):
    led = Ledger(path, private_path=path / "private", clock=_clock())
    for i in range(n):
        led.declare(f"t{i}", p, agent, "1")
        led.resolve(f"t{i}", i < hits, agent, "1")
    return led


def make_jsonl(path, rows):
    path.write_text("".join(json.dumps({"id": f"q{i}", "confidence": c, "correct": ok}) + "\n"
                            for i, (c, ok) in enumerate(rows)))
    return path


SCORE_KEYS = ("brier", "brier_ci95", "ocw_share", "ocw_ci95", "ece")


# --- rule 1: minimum 50 answered items; below it no score and exit 0 ---------------------------------------------------
def test_49_items_report_exit_0_and_no_score(tmp_path):
    make_ledger(tmp_path / "cur", 0.9, 0, 49)                 # terrible, but 49 items
    code, rep = ci_gate.check(tmp_path / "cur", tmp_path / "b.json")
    assert code == 0 and rep["result"] == "not enough data" and rep["n"] == 49
    assert not any(k in rep for k in SCORE_KEYS)
    assert not (tmp_path / "b.json").exists()                 # no baseline from 49 items
    md = ci_gate.markdown(code, rep)
    assert "NOT ENOUGH DATA" in md and "| Brier |" not in md


def test_50_items_is_enough(tmp_path):
    make_ledger(tmp_path / "cur", 0.9, 45, 50)
    code, rep = ci_gate.check(tmp_path / "cur", tmp_path / "b.json")
    assert rep["n"] == 50 and "brier" in rep and rep["result"] == "inconclusive: baseline created" and code == 0


# --- rule 3: no baseline -> created, exit 0; --strict -> exit 2 -------------------------------------------------------
def test_no_baseline_creates_it_and_passes(tmp_path):
    make_ledger(tmp_path / "cur", 0.9, 90, 100)
    code, rep = ci_gate.check(tmp_path / "cur", tmp_path / "b.json")
    base = json.loads((tmp_path / "b.json").read_text())
    assert code == 0 and rep["result"] == "inconclusive: baseline created"
    assert base["n"] == 100 and "ocw_share" in base and "brier" in base


def test_no_baseline_with_strict_exits_2_but_still_creates_it(tmp_path):
    make_ledger(tmp_path / "cur", 0.9, 90, 100)
    code, rep = ci_gate.check(tmp_path / "cur", tmp_path / "b.json", strict=True)
    assert code == 2 and rep["result"] == "inconclusive: baseline created" and (tmp_path / "b.json").exists()
    assert main(["gate", str(tmp_path / "cur"), "--baseline", str(tmp_path / "b2.json"), "--strict"]) == 2
    assert main(["gate", str(tmp_path / "cur"), "--baseline", str(tmp_path / "b3.json")]) == 0


# --- rule 2 + 4: conservative fail rule on Brier and on the overconfident-wrong share ---------------------------------
def test_pass_on_same_calibration(tmp_path):
    make_ledger(tmp_path / "base", 0.9, 90, 100)
    make_ledger(tmp_path / "cur", 0.9, 88, 100)
    ci_gate.write_baseline(tmp_path / "base", tmp_path / "b.json")
    code, rep = ci_gate.check(tmp_path / "cur", tmp_path / "b.json")
    assert code == 0 and rep["result"] == "pass"


def test_fail_when_much_worse_beyond_interval(tmp_path):
    make_ledger(tmp_path / "base", 0.9, 90, 100)
    make_ledger(tmp_path / "cur", 0.9, 50, 100)               # says 90%, right half the time
    ci_gate.write_baseline(tmp_path / "base", tmp_path / "b.json")
    code, rep = ci_gate.check(tmp_path / "cur", tmp_path / "b.json")
    assert code == 1 and "Brier" in rep["reason"] and "overconfident-wrong share" in rep["reason"]


def test_point_worse_but_interval_overlaps_is_not_a_fail(tmp_path):
    make_ledger(tmp_path / "base", 0.9, 90, 100)
    make_ledger(tmp_path / "cur", 0.9, 42, 50)                # point estimate worse, interval wide at n=50
    ci_gate.write_baseline(tmp_path / "base", tmp_path / "b.json")
    code, rep = ci_gate.check(tmp_path / "cur", tmp_path / "b.json")
    assert rep["brier_delta"] > 0.02 and code == 0 and "not counted as a regression" in rep["reason"]


def test_overconfident_wrong_alone_can_fail(tmp_path):
    # Brier threshold set huge, so only the overconfident-wrong share can fail the run
    rows_base = [(0.95, True)] * 95 + [(0.95, False)] * 5
    rows_cur = [(0.95, True)] * 60 + [(0.95, False)] * 40
    make_jsonl(tmp_path / "base.jsonl", rows_base); make_jsonl(tmp_path / "cur.jsonl", rows_cur)
    ci_gate.write_baseline(tmp_path / "base.jsonl", tmp_path / "b.json")
    code, rep = ci_gate.check(tmp_path / "cur.jsonl", tmp_path / "b.json", max_brier_delta=10.0)
    assert code == 1 and rep["reason"].startswith("overconfident-wrong share")


def test_ece_and_abstain_rate_are_reported_but_never_fail(tmp_path):
    base = [(0.7, True)] * 70 + [(0.7, False)] * 30
    cur = [(0.7, True)] * 70 + [(0.7, False)] * 30 + [(None, False)] * 100      # half the items abstained
    make_jsonl(tmp_path / "base.jsonl", base); make_jsonl(tmp_path / "cur.jsonl", cur)
    ci_gate.write_baseline(tmp_path / "base.jsonl", tmp_path / "b.json")
    code, rep = ci_gate.check(tmp_path / "cur.jsonl", tmp_path / "b.json")
    assert code == 0 and rep["abstain_rate"] == 0.5 and "ece" in rep
    md = ci_gate.markdown(code, rep)
    assert "| ECE (10 bins) |" in md and "| Abstain rate |" in md and md.count("no, report only") == 2


# --- rule 5: JSONL input -------------------------------------------------------------------------------------------------
def test_all_null_input_is_not_enough_data(tmp_path):
    make_jsonl(tmp_path / "x.jsonl", [(None, True)] * 80)
    code, rep = ci_gate.check(tmp_path / "x.jsonl", tmp_path / "b.json")
    assert code == 0 and rep["result"] == "not enough data" and rep["n"] == 0 and rep["abstain_rate"] == 1.0
    assert not any(k in rep for k in SCORE_KEYS) and not (tmp_path / "b.json").exists()


def test_null_confidence_counts_only_for_abstain_rate(tmp_path):
    rows = [(0.8, True)] * 40 + [(0.8, False)] * 10 + [(None, True)] * 30 + [(None, False)] * 20
    make_jsonl(tmp_path / "x.jsonl", rows)
    m = ci_gate.measure(tmp_path / "x.jsonl")
    assert m["n"] == 50 and m["items"] == 100 and m["abstained"] == 50 and m["abstain_rate"] == 0.5
    assert abs(m["brier"] - (40 * 0.04 + 10 * 0.64) / 50) < 1e-12 and m["ocw_share"] == 0.0


def test_jsonl_and_ledger_give_identical_scores(tmp_path):
    rows = [(0.9, True)] * 40 + [(0.95, False)] * 7 + [(0.6, True)] * 20 + [(0.6, False)] * 13
    make_jsonl(tmp_path / "x.jsonl", rows)
    led = Ledger(tmp_path / "led", private_path=tmp_path / "led" / "private")
    for i, (c, ok) in enumerate(rows):
        led.declare(f"q{i}", c, "a", "1"); led.resolve(f"q{i}", ok, "a", "1")
    mj, ml = ci_gate.measure(tmp_path / "x.jsonl"), ci_gate.measure(tmp_path / "led")
    for k in SCORE_KEYS + ("n",):
        assert mj[k] == ml[k], k
    assert mj["input"] == "jsonl" and ml["input"] == "ledger"


@pytest.mark.parametrize("line", ['{"id": "a", "confidence": 1.5, "correct": true}',
                                  '{"id": "a", "confidence": 0.5, "correct": "yes"}',
                                  '{"id": "a", "correct": true}', 'not json'])
def test_bad_jsonl_fails_loudly(tmp_path, line):
    (tmp_path / "x.jsonl").write_text(line + "\n")
    code, rep = ci_gate.check(tmp_path / "x.jsonl", tmp_path / "b.json")
    assert code == 1 and rep["result"] == "fail"


def test_duplicate_jsonl_ids_are_refused(tmp_path):
    (tmp_path / "x.jsonl").write_text('{"id":"a","confidence":0.5,"correct":true}\n{"id":"a","confidence":0.6,"correct":false}\n')
    assert ci_gate.check(tmp_path / "x.jsonl", tmp_path / "b.json")[0] == 1


# --- rule 6: thresholds configurable and labelled ------------------------------------------------------------------------
def test_thresholds_are_labelled_in_every_report(tmp_path):
    make_ledger(tmp_path / "small", 0.9, 9, 10)
    make_ledger(tmp_path / "big", 0.9, 90, 100)
    reps = [ci_gate.check(tmp_path / "small", tmp_path / "b.json")[1],
            ci_gate.check(tmp_path / "big", tmp_path / "b.json")[1],
            ci_gate.check(tmp_path / "big", tmp_path / "b.json", max_brier_delta=0.05, max_ocw_delta=0.1)[1]]
    for r in reps:
        assert r["thresholds"]["label"] == "QANTS starting values, not industry standards"
        assert "QANTS starting values, not industry standards" in ci_gate.markdown(0, r)
    assert reps[2]["thresholds"]["max_ocw_delta"] == 0.1


# --- integrity and CLI ---------------------------------------------------------------------------------------------------
def test_tampered_ledger_fails(tmp_path):
    make_ledger(tmp_path / "cur", 0.9, 90, 100)
    f = tmp_path / "cur" / "resolutions.jsonl"
    lines = f.read_text().splitlines()
    rec = json.loads(lines[3]); rec["outcome"] = 1 - rec["outcome"]; lines[3] = json.dumps(rec)
    f.write_text("\n".join(lines) + "\n")
    code, rep = ci_gate.check(tmp_path / "cur", tmp_path / "b.json")
    assert code == 1 and "does not verify" in rep["reason"]


def test_cli_exit_codes_and_markdown_summary(tmp_path):
    make_ledger(tmp_path / "base", 0.9, 90, 100)
    make_ledger(tmp_path / "bad", 0.9, 50, 100)
    b = str(tmp_path / "b.json"); md = tmp_path / "summary.md"
    assert main(["gate", str(tmp_path / "base"), "--baseline", b, "--write-baseline"]) == 0
    assert main(["gate", str(tmp_path / "base"), "--baseline", b, "--summary-md", str(md)]) == 0
    assert main(["gate", str(tmp_path / "bad"), "--baseline", b, "--summary-md", str(md)]) == 1
    text = md.read_text()
    assert "Mizan calibration gate: PASS" in text and "Mizan calibration gate: FAIL" in text
    assert "| Brier |" in text and "Overconfident-wrong share" in text
