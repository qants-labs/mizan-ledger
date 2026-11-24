"""Baseline persistence in action.yml: saved to the Actions cache on default-branch pushes, restored on pull requests.

GitHub's cache service cannot run here, so test_push_then_pr_sequence replays the action's steps with a small local
stand-in for actions/cache (exact key, else the newest entry whose key starts with a restore-key prefix).
"""
import json, pathlib, shutil
import pytest
from mizan_ledger.cli import main

ROOT = pathlib.Path(__file__).resolve().parents[1]


class FakeCache:
    """Stand-in for actions/cache: save(key, file); restore(key, restore_keys) -> newest prefix match."""
    def __init__(self, root):
        self.root, self.order = root, []
        root.mkdir(parents=True, exist_ok=True)

    def save(self, key, path):
        if path.exists():
            shutil.copy(path, self.root / key); self.order.append(key)

    def restore(self, key, restore_keys, path):
        if (self.root / key).exists():
            hit = key
        else:
            hit = next((k for k in reversed(self.order) for p in restore_keys if k.startswith(p)), None)
        if hit:
            path.parent.mkdir(parents=True, exist_ok=True); shutil.copy(self.root / hit, path)
        return hit


def results(path, wrong_at_095):
    rows = [{"id": f"q{i}", "confidence": 0.95, "correct": i >= wrong_at_095} for i in range(100)]
    path.write_text("".join(json.dumps(r) + "\n" for r in rows))
    return path


def run_action(workdir, cache, event, run_id, agent, wrong):
    """The action's steps, in order, for a JSONL input on a fresh runner (empty workspace)."""
    ws = workdir / f"run-{run_id}"; ws.mkdir()
    baseline = ws / "mizan-baseline.json"
    inp = results(ws / "results.jsonl", wrong)
    if event == "pull_request":
        cache.restore(f"mizan-baseline-{agent}-pr-{run_id}", [f"mizan-baseline-{agent}-"], baseline)
    summary = ws / "summary.md"
    code = main(["gate", str(inp), "--baseline", str(baseline), "--summary-md", str(summary)])
    if event == "push_default" and baseline.exists():
        cache.save(f"mizan-baseline-{agent}-{run_id}", baseline)
    return code, summary.read_text()


def test_push_then_pr_sequence(tmp_path):
    cache = FakeCache(tmp_path / "cache")
    # 1. first pull request before anything was pushed: no baseline in the cache -> created, exit 0
    code, md = run_action(tmp_path, cache, "pull_request", 1, "bot", wrong=5)
    assert code == 0 and "BASELINE CREATED" in md and cache.order == []          # a PR never saves
    # 2. push to main: no baseline restored on push -> created from this run, saved to the cache
    code, md = run_action(tmp_path, cache, "push_default", 2, "bot", wrong=5)
    assert code == 0 and cache.order == ["mizan-baseline-bot-2"]
    # 3. a PR that keeps calibration: restored baseline -> pass
    code, md = run_action(tmp_path, cache, "pull_request", 3, "bot", wrong=6)
    assert code == 0 and "Mizan calibration gate: PASS" in md
    # 4. a PR that gets far more confident-and-wrong: restored baseline -> fail
    code, md = run_action(tmp_path, cache, "pull_request", 4, "bot", wrong=40)
    assert code == 1 and "Mizan calibration gate: FAIL" in md
    # 5. a later push to main becomes the newest baseline, and the next PR compares against it
    run_action(tmp_path, cache, "push_default", 5, "bot", wrong=40)
    code, md = run_action(tmp_path, cache, "pull_request", 6, "bot", wrong=40)
    assert code == 0 and cache.order == ["mizan-baseline-bot-2", "mizan-baseline-bot-5"]
    # 6. another agent id never picks up this agent's baseline
    code, md = run_action(tmp_path, cache, "pull_request", 7, "other-bot", wrong=40)
    assert code == 0 and "BASELINE CREATED" in md


def test_action_yml_cache_steps():
    yaml = pytest.importorskip("yaml")
    steps = yaml.safe_load((ROOT / "action.yml").read_text())["runs"]["steps"]
    restore = next(s for s in steps if s.get("uses", "").startswith("actions/cache/restore@"))
    save = next(s for s in steps if s.get("uses", "").startswith("actions/cache/save@"))
    gate = next(i for i, s in enumerate(steps) if s.get("id") == "gate")
    assert steps.index(restore) < gate < steps.index(save)
    assert "github.event_name == 'pull_request'" in restore["if"] and "committed != 'true'" in restore["if"]
    assert restore["with"]["restore-keys"].strip() == "mizan-baseline-${{ inputs.agent-id }}-"
    assert save["with"]["key"] == "mizan-baseline-${{ inputs.agent-id }}-${{ github.run_id }}"
    assert "github.event_name == 'push'" in save["if"] and "default_branch" in save["if"] and "committed != 'true'" in save["if"]
