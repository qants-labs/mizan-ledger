import pytest
from mizan_ledger import Ledger, LedgerError
from mizan_ledger.gate import should_act, ACT, ABSTAIN, MIN_N_FOR_ADJUSTMENT


def _clock():
    n = [0]
    def c():
        n[0] += 1
        return f"2026-11-23T01:{n[0] // 60:02d}:{n[0] % 60:02d}.000Z"
    return c


def ledger_with(path, p, hits, n, agent="a"):
    led = Ledger(path, private_path=path / "private", clock=_clock())
    for i in range(n):
        led.declare(f"t{i}", p, agent, "1")
        led.resolve(f"t{i}", i < hits, agent, "1")
    return led


def test_threshold_rule_without_ledger():
    assert should_act(0.96, 0.95).decision == ACT
    d = should_act(0.80, 0.95)
    assert d.decision == ABSTAIN and not d.act and "no ledger" in d.reason and d.p_adjusted is None


def test_small_record_uses_raw_p_and_says_so(tmp_path):
    led = ledger_with(tmp_path, 0.95, 5, 10)                 # overconfident, but only 10 records
    d = should_act(0.96, 0.95, ledger=led, agent_id="a")
    assert d.act and not d.adjusted and d.n_resolved == 10
    assert "without adjustment" in d.reason


def test_overconfident_agent_is_adjusted_down_and_abstains(tmp_path):
    led = ledger_with(tmp_path, 0.95, 30, 60)                # says 95%, right 50% of the time
    d = should_act(0.96, 0.95, ledger=led, agent_id="a")
    assert d.n_resolved == 60 >= MIN_N_FOR_ADJUSTMENT and d.adjusted
    assert d.p_raw == 0.96 and d.p_adjusted < 0.95 and d.decision == ABSTAIN
    assert d.bin["n_bin"] == 60 and d.bin["freq_bin"] == 0.5


def test_well_calibrated_agent_keeps_acting(tmp_path):
    led = ledger_with(tmp_path, 0.97, 59, 60)
    d = should_act(0.97, 0.95, ledger=led, agent_id="a")
    assert d.adjusted and d.p_adjusted >= 0.95 and d.decision == ACT


def test_empty_bin_falls_back_to_raw_p(tmp_path):
    led = ledger_with(tmp_path, 0.55, 30, 60)
    d = should_act(0.99, 0.95, ledger=led, agent_id="a")
    assert not d.adjusted and d.act and "no past records" in d.reason


def test_commit_declares_before_the_action_so_abstentions_are_scored(tmp_path):
    led = Ledger(tmp_path, private_path=tmp_path / "private")
    d = should_act(0.6, 0.9, ledger=led, agent_id="a", task_id="delete-prod-db", commit=True)
    assert d.decision == ABSTAIN and d.receipt["agent_id"] == "a"
    assert led.summary("a")["declared"] == 1 and led.summary("a")["pending"] == 1
    with pytest.raises(LedgerError):
        should_act(0.6, 0.9, ledger=led, agent_id="a", task_id="delete-prod-db", commit=True)   # no replacement
    with pytest.raises(LedgerError):
        should_act(0.6, 0.9, commit=True)


def test_rejects_bad_numbers():
    for p, t in ((1.2, 0.5), (0.5, -0.1), (float("nan"), 0.5)):
        with pytest.raises(LedgerError):
            should_act(p, t)
