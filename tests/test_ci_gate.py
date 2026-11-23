import json
from mizan_ledger import Ledger
from mizan_ledger import ci_gate
from mizan_ledger.cli import main


def _clock():
    n = [0]
    def c():
        n[0] += 1
        return f"2026-11-23T00:{n[0] // 60:02d}:{n[0] % 60:02d}.000Z"
    return c


def make_ledger(path, p, hits, n, agent="a"):
    led = Ledger(path, private_path=path / "private", clock=_clock())
    for i in range(n):
        led.declare(f"t{i}", p, agent, "1")
        led.resolve(f"t{i}", i < hits, agent, "1")
    return led


def test_write_baseline_then_pass_on_same_calibration(tmp_path):
    make_ledger(tmp_path / "base", 0.9, 90, 100)
    make_ledger(tmp_path / "cur", 0.9, 88, 100)
    code, rep = ci_gate.write_baseline(tmp_path / "base", tmp_path / "b.json", min_n=50)
    assert code == ci_gate.PASS and json.loads((tmp_path / "b.json").read_text())["n"] == 100
    code, rep = ci_gate.check(tmp_path / "cur", tmp_path / "b.json", 0.02, 50)
    assert code == ci_gate.PASS, rep["reason"]


def test_fail_when_much_worse_beyond_interval(tmp_path):
    make_ledger(tmp_path / "base", 0.9, 90, 100)
    make_ledger(tmp_path / "cur", 0.9, 50, 100)        # says 90%, right half the time
    ci_gate.write_baseline(tmp_path / "base", tmp_path / "b.json")
    code, rep = ci_gate.check(tmp_path / "cur", tmp_path / "b.json", 0.02, 50)
    assert code == ci_gate.FAIL and rep["brier_ci95"][0] - rep["baseline"]["brier"] > 0.02


def test_point_worse_but_interval_overlaps_is_not_a_fail(tmp_path):
    make_ledger(tmp_path / "base", 0.9, 90, 100)
    make_ledger(tmp_path / "cur", 0.9, 42, 50)          # point estimate worse by > 0.02, interval wide at n=50
    ci_gate.write_baseline(tmp_path / "base", tmp_path / "b.json")
    code, rep = ci_gate.check(tmp_path / "cur", tmp_path / "b.json", 0.02, 50)
    assert rep["brier_delta"] > 0.02
    assert code == ci_gate.PASS and "not counted as a regression" in rep["reason"]


def test_small_sample_is_inconclusive_never_pass_or_fail(tmp_path):
    make_ledger(tmp_path / "base", 0.9, 90, 100)
    make_ledger(tmp_path / "cur", 0.9, 0, 10)           # terrible, but n=10
    ci_gate.write_baseline(tmp_path / "base", tmp_path / "b.json")
    code, rep = ci_gate.check(tmp_path / "cur", tmp_path / "b.json", 0.02, 50)
    assert code == ci_gate.INCONCLUSIVE and "min_n" in rep["reason"]


def test_no_baseline_is_inconclusive_and_small_baseline_is_refused(tmp_path):
    make_ledger(tmp_path / "cur", 0.9, 9, 10)
    assert ci_gate.check(tmp_path / "cur", tmp_path / "missing.json")[0] == ci_gate.INCONCLUSIVE
    code, _ = ci_gate.write_baseline(tmp_path / "cur", tmp_path / "b.json", min_n=50)
    assert code == ci_gate.INCONCLUSIVE and not (tmp_path / "b.json").exists()


def test_tampered_ledger_fails(tmp_path):
    make_ledger(tmp_path / "base", 0.9, 90, 100)
    ci_gate.write_baseline(tmp_path / "base", tmp_path / "b.json")
    make_ledger(tmp_path / "cur", 0.9, 90, 100)
    f = tmp_path / "cur" / "resolutions.jsonl"
    lines = f.read_text().splitlines()
    rec = json.loads(lines[3]); rec["outcome"] = 1 - rec["outcome"]; lines[3] = json.dumps(rec)
    f.write_text("\n".join(lines) + "\n")
    code, rep = ci_gate.check(tmp_path / "cur", tmp_path / "b.json")
    assert code == ci_gate.FAIL and "does not verify" in rep["reason"]


def test_cli_exit_codes_and_markdown_summary(tmp_path, capsys):
    make_ledger(tmp_path / "base", 0.9, 90, 100)
    make_ledger(tmp_path / "bad", 0.9, 50, 100)
    b = str(tmp_path / "b.json"); md = tmp_path / "summary.md"
    assert main(["gate", str(tmp_path / "base"), "--baseline", b, "--write-baseline"]) == 0
    assert main(["gate", str(tmp_path / "base"), "--baseline", b, "--summary-md", str(md)]) == 0
    assert main(["gate", str(tmp_path / "bad"), "--baseline", b, "--summary-md", str(md)]) == 1
    text = md.read_text()
    assert "Mizan calibration gate: PASS" in text and "Mizan calibration gate: FAIL" in text
    assert "| Brier |" in text and "| ECE |" in text and "95% interval" in text
