import importlib.util, pathlib, pytest
from mizan_ledger import Ledger

ROOT = pathlib.Path(__file__).resolve().parents[1]


def _promptfoo():
    spec = importlib.util.spec_from_file_location("mizan_assert", ROOT / "examples" / "promptfoo" / "mizan_assert.py")
    mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
    return mod


def test_promptfoo_assert_records_and_scores(tmp_path):
    m = _promptfoo()
    cfg = {"ledger_dir": str(tmp_path), "agent_id": "pf", "run_id": "r1"}
    r = m.get_assert("Paris. Confidence: 0.9", {"vars": {"expected": "Paris", "question": "q1"}, "config": cfg, "prompt": "p"})
    assert r["pass"] is True and abs(r["score"] - (1 - 0.01)) < 1e-9
    r = m.get_assert("Lyon. confidence=0.8", {"vars": {"expected": "Paris", "question": "q2"}, "config": cfg, "prompt": "p"})
    assert r["pass"] is False and abs(r["score"] - (1 - 0.64)) < 1e-9
    s = Ledger(tmp_path).summary("pf")
    assert s["declared"] == 2 and s["resolved"] == 2 and abs(s["brier"] - (0.01 + 0.64) / 2) < 1e-9


def test_promptfoo_no_confidence_is_not_invented(tmp_path):
    m = _promptfoo()
    r = m.get_assert("Paris", {"vars": {"expected": "Paris"}, "config": {"ledger_dir": str(tmp_path)}})
    assert r["pass"] is False and "no stated confidence" in r["reason"]
    assert not (tmp_path / "receipts.jsonl").exists()


def test_inspect_scorer_offline(tmp_path):
    """Calls the scorer directly on TaskStates (a full `inspect eval` downloads a tokenizer file, so it is not run here)."""
    pytest.importorskip("inspect_ai")
    import asyncio
    from inspect_ai.model import ModelName, ModelOutput
    from inspect_ai.scorer import Target, CORRECT, INCORRECT
    from inspect_ai.solver import TaskState
    from mizan_ledger.adapters.inspect_scorer import mizan_calibration, brier
    sc = mizan_calibration(inner="includes", ledger_dir=str(tmp_path), agent_id="m", run_id="r1")

    def state(sid, text, meta=None):
        return TaskState(ModelName("mockllm/model"), sid, 1, "q", [], output=ModelOutput.from_content("mockllm/model", text),
                         metadata=meta or {})
    s1 = asyncio.run(sc(state("s1", "Paris. Confidence: 0.9"), Target("Paris")))
    s2 = asyncio.run(sc(state("s2", "Lyon. confidence=0.8"), Target("Paris")))
    s3 = asyncio.run(sc(state("s3", "Rome"), Target("Rome")))
    s4 = asyncio.run(sc(state("s5", "Rome", {"confidence": 0.6}), Target("Rome")))
    assert s1.value == CORRECT and abs(s1.metadata["squared_error"] - 0.01) < 1e-9
    assert s2.value == INCORRECT and abs(s2.metadata["squared_error"] - 0.64) < 1e-9
    assert s3.value == CORRECT and "no stated confidence" in s3.metadata["mizan"] and "squared_error" not in s3.metadata
    assert abs(s4.metadata["confidence"] - 0.6) < 1e-9
    assert abs(brier()([s1, s2, s3, s4]) - (0.01 + 0.64 + 0.16) / 3) < 1e-9
    s = Ledger(tmp_path).summary("m")
    assert s["declared"] == 3 and s["resolved"] == 3


def test_inspect_entry_point_resolves_scorer_by_name():
    pytest.importorskip("inspect_ai")
    from importlib.metadata import entry_points
    eps = [e for e in entry_points(group="inspect_ai") if e.name == "mizan_ledger"]
    assert eps and eps[0].value == "mizan_ledger.adapters._inspect_registry"
    import subprocess, sys   # fresh process: the name must resolve through the entry point alone
    code = ("from inspect_ai._util.registry import registry_lookup as r; "
            "import sys; sys.exit(0 if r('scorer', 'mizan_ledger/mizan_calibration') else 1)")
    assert subprocess.run([sys.executable, "-c", code], capture_output=True).returncode == 0
