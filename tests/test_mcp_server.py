"""Optional MCP extra. Skipped when the `mcp` package is not installed."""
import pathlib
import pytest

pytest.importorskip("mcp")
from mizan_ledger import mcp_server as srv  # noqa: E402

SEEDS = pathlib.Path(__file__).resolve().parents[1] / "seed_runs"


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("MIZAN_LEDGER_DIR", str(tmp_path / "pub"))
    monkeypatch.setenv("MIZAN_PRIVATE_DIR", str(tmp_path / "priv"))
    monkeypatch.setenv("MIZAN_AGENT_ID", "t-agent")
    return tmp_path


def test_round_trip(env):
    r = srv.commit_confidence("task-1", 0.8)

    assert "salt" not in r and "opening" not in r
    res = srv.resolve_outcome("task-1", True)
    assert res["outcome"] == 1 and res["opening"]["p"] == "0.800000"
    s = srv.get_calibration()
    assert s["declared"] == 1 and s["resolved"] == 1 and s["pending"] == 0
    v = srv.verify_ledger()
    assert v["ok"] is True


def test_rejects_bad_input(env):
    for bad in (-0.1, 1.5, True, "0.5"):
        with pytest.raises(ValueError):
            srv.commit_confidence("x", bad)
    srv.commit_confidence("dup", 0.5)
    with pytest.raises(ValueError):
        srv.commit_confidence("dup", 0.6)
    with pytest.raises(ValueError):
        srv.resolve_outcome("dup", "yes")
    srv.resolve_outcome("dup", False)
    with pytest.raises(ValueError):
        srv.resolve_outcome("dup", True)  # outcomes cannot be changed


def test_read_only_tools_do_not_create_dirs(env):
    s = srv.get_calibration()
    assert s["n"] == 0
    assert not (env / "pub").exists()
    with pytest.raises(ValueError):
        srv.verify_ledger()
    assert not (env / "pub").exists()


def test_private_opening_not_in_public_dir(env):
    srv.commit_confidence("t", 0.3)
    pub = (env / "pub" / "receipts.jsonl").read_text()
    assert "salt" not in pub and "0.300000" not in pub
    assert (env / "priv" / "openings.jsonl").exists()


@pytest.mark.skipif(not SEEDS.is_dir(), reason="seed_runs/ is not shipped in the sdist")
def test_verify_seed_ledger():
    rep = srv.verify_ledger(str(SEEDS / "qants-seed-codetask"))
    assert rep["ok"] and rep["summary"]["declared"] == 60 and rep["summary"]["resolved"] == 60


def test_no_network_or_exec_imports():
    import ast
    tree = ast.parse(pathlib.Path(srv.__file__).read_text())
    mods = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            mods |= {a.name.split(".")[0] for a in n.names}
        elif isinstance(n, ast.ImportFrom) and n.module and n.level == 0:
            mods.add(n.module.split(".")[0])
        elif isinstance(n, ast.Call) and isinstance(n.func, ast.Name):
            assert n.func.id not in ("eval", "exec", "compile", "__import__")
        elif isinstance(n, ast.Attribute):
            assert n.attr not in ("system", "popen", "spawn")
    assert mods <= {"__future__", "os", "pathlib", "typing", "mcp"}, mods


def test_should_act_tool(env):
    r = srv.should_act(0.5, 0.9)
    assert r["decision"] == "ABSTAIN" and r["adjusted"] is False and "agent_id" in r
    assert not (env / "pub").exists()                      # read-only without commit
    r = srv.should_act(0.95, 0.9, task_id="send-email", commit=True)
    assert r["decision"] == "ACT" and r["receipt"]["seq"] == 1
    assert srv.get_calibration()["declared"] == 1
    for bad in (True, "0.9", 1.5):
        with pytest.raises(ValueError):
            srv.should_act(bad, 0.9)
