import json
from mizan_ledger import Ledger
from mizan_ledger.cli import main
from mizan_ledger.otel_export import events, EVENT_NAME


def _ledger(tmp_path):
    led = Ledger(tmp_path, private_path=tmp_path / "private")
    led.declare("t1", 0.9, "a", "1"); led.resolve("t1", True, "a", "1")
    led.declare("t2", 0.7, "a", "1"); led.resolve("t2", False, "a", "1")
    led.declare("t3", 0.5, "b", "1")                       # pending: not exported
    return led


def test_events_shape(tmp_path):
    _ledger(tmp_path)
    ev = list(events(tmp_path))
    assert len(ev) == 2 and all(e["name"] == EVENT_NAME for e in ev)
    a = ev[1]["attributes"]
    assert a["gen_ai.evaluation.name"] == "mizan.calibration" and a["gen_ai.evaluation.score.label"] == "incorrect"
    assert abs(a["gen_ai.evaluation.score.value"] - 0.49) < 1e-9 and a["mizan.task_id"] == "t2"
    assert len(a["mizan.commitment"]) == 64 and "stated confidence 0.700" in a["gen_ai.evaluation.explanation"]


def test_cli_stdout_file_and_filter(tmp_path, capsys):
    _ledger(tmp_path)
    assert main(["export-otel", str(tmp_path)]) == 0
    lines = capsys.readouterr().out.strip().splitlines()
    assert len(lines) == 2 and json.loads(lines[0])["attributes"]["mizan.p_stated"] == 0.9
    out = tmp_path / "ev.jsonl"
    assert main(["export-otel", str(tmp_path), "--agent", "b", "--out", str(out)]) == 0 and out.read_text() == ""


def test_tampered_ledger_is_not_exported(tmp_path, capsys):
    _ledger(tmp_path)
    f = tmp_path / "resolutions.jsonl"
    f.write_text(f.read_text().replace('"outcome":0', '"outcome":1'))
    assert main(["export-otel", str(tmp_path)]) == 1
    assert capsys.readouterr().out == ""
