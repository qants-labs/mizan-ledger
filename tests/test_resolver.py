"""Hardening v1 T4: verifiable resolution. No network: live fetches are replayed from recorded snapshots."""
import copy, json, subprocess, sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from mizan_ledger import resolver as R, yamlmini

ROOT = Path(__file__).resolve().parents[1]
RES, QS, FIX = ROOT / "resolvers", ROOT / "questions", ROOT / "tests" / "fixtures" / "resolver"
REQUIRED_KEYS = {"question_id", "outcome", "confidence", "sources", "raw_values", "resolved_at", "resolver_version", "resolver_hash"}
EIGHT = ("entity", "period", "sources", "normalization", "tolerance", "missing_or_late", "outcome_rule", "reproduce")
REFS = ["ref_xrp_2026_11_25", "ref_spy_2026_11_24", "ref_gh_2026_11_24"]


def load(qid):
    q = R.load_question(QS, qid)
    rv, rh = R.load_resolver(RES, q["resolution_rule"]["resolver"])
    return q, rv, rh


def snap(qid):
    return json.loads((FIX / f"{qid}.snapshot.json").read_text())


def entry(source, url, body, status=200):
    return {"source": source, "url": url, "request_time": "2026-11-26T00:00:00Z", "status": status, "headers": {},
            "body_sha256": "sha256:" + R.sha256_hex(body), "body": body}


@pytest.mark.parametrize("name", ["xrp_daily_close", "spy_daily_close", "github_commits"])
def test_resolver_files_have_all_eight_items_and_two_keyless_sources(name):
    doc, h = R.load_resolver(RES, name)
    for k in EIGHT:
        assert doc[k] not in (None, "", [], {}), k
    assert len(doc["sources"]) == 2 and len({s["id"] for s in doc["sources"]}) == 2
    assert any(s["fallback_pre_approved"] for s in doc["sources"])
    assert doc["reproduce"] == "mizan resolve --question-id {question_id} --reproduce"
    assert doc["version"] == "1.0.0" and h.startswith("sha256:") and len(h) == 71


def test_resolver_missing_item_or_keyed_source_is_rejected(tmp_path):
    text = (RES / "xrp_daily_close.yaml").read_text()
    (tmp_path / "xrp_daily_close.yaml").write_text(text.replace("\ntolerance:", "\ntolerance_x:"))
    with pytest.raises(ValueError, match="missing required field"):
        R.load_resolver(tmp_path, "xrp_daily_close")
    (tmp_path / "xrp_daily_close.yaml").write_text(text.replace("localization=false", "localization=false&api_key=x"))
    with pytest.raises(ValueError, match="keyless"):
        R.load_resolver(tmp_path, "xrp_daily_close")


def test_yamlmini_reads_like_pyyaml():
    yaml = pytest.importorskip("yaml")
    for p in sorted(RES.glob("*.yaml")):
        assert yamlmini.loads(p.read_text()) == yaml.safe_load(p.read_text()), p.name


def test_yamlmini_refuses_unsupported_constructs():
    for bad in ["a: YES", "a: no", "a: &x 1", "a: {b: 1}", "a: |\n  x", "a: 1\na: 2", "a:\n\t- 1"]:
        with pytest.raises(ValueError):
            yamlmini.loads(bad)


@pytest.mark.parametrize("qid", REFS + ["q_2026_11_26_001"])
def test_questions_are_published_with_rule_sources_time_and_hash(qid):
    q, rv, rh = load(qid)
    assert set(R.QUESTION_FIELDS) <= set(q)
    assert R.check_question(q, rv, rh) == []
    assert q["resolution_rule"]["resolver_hash"] == rh and q["resolution_rule"]["resolver_version"] == rv["version"]
    assert R.parse_utc(q["created_at"]) < R.parse_utc(q["deadline"])


def test_editing_a_question_or_its_resolver_breaks_the_hash(tmp_path):
    q, rv, rh = load("q_2026_11_26_001")
    q2 = copy.deepcopy(q); q2["resolution_rule"]["threshold"] = 0.96
    assert any("question_hash" in e for e in R.check_question(q2, rv, rh))
    (tmp_path / "xrp_daily_close.yaml").write_text((RES / "xrp_daily_close.yaml").read_text().replace("relative_pct: 0.5", "relative_pct: 5"))
    rv2, rh2 = R.load_resolver(tmp_path, "xrp_daily_close")
    assert rh2 != rh and R.check_question(q, rv2, rh2)


@pytest.mark.parametrize("qid", REFS)
def test_reproduce_from_snapshot_gives_the_published_resolution(qid):
    published = json.loads((FIX / f"{qid}.resolution.json").read_text())
    again = R.reproduce(qid, QS, RES, snapshot=FIX / f"{qid}.snapshot.json")
    assert set(again) == REQUIRED_KEYS
    assert again == published
    q, rv, _ = load(qid)
    assert R.reconcile(published, again, rv, q)["status"] == "ACCEPTED"
    assert published["confidence"] == "high" and published["outcome"] in ("YES", "NO")


def test_cli_reproduce_prints_exactly_the_agreed_keys(tmp_path):
    qid = "ref_xrp_2026_11_25"
    cmd = [sys.executable, "-m", "mizan_ledger.cli", "resolve", "--question-id", qid, "--reproduce",
           "--questions", str(QS), "--resolvers", str(RES), "--snapshot", str(FIX / f"{qid}.snapshot.json"),
           "--compare", str(FIX / f"{qid}.resolution.json")]
    r = subprocess.run(cmd, capture_output=True, text=True, env={"PYTHONPATH": str(ROOT / "src"), "PATH": "/usr/bin:/bin"})
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)
    assert set(out) == REQUIRED_KEYS and out["resolver_version"] == "1.0.0" and out["resolver_hash"].startswith("sha256:")
    assert '"status": "ACCEPTED"' in r.stderr


def test_sources_log_url_request_time_and_raw_body_hash():
    s = snap("ref_xrp_2026_11_25")
    for e in s["log"]:
        assert e["url"].startswith("https://") and e["request_time"].endswith("Z")
        assert e["body_sha256"] == "sha256:" + R.sha256_hex(e["body"])
        assert set(e["headers"]) <= {"x-ratelimit-limit", "x-ratelimit-remaining", "x-ratelimit-reset", "x-ratelimit-used", "x-ratelimit-resource"}


def test_tampered_snapshot_is_refused():
    s = snap("ref_xrp_2026_11_25")
    s["log"][0]["body"] = s["log"][0]["body"].replace("0.92", "0.99", 1)
    q, rv, rh = load("ref_xrp_2026_11_25")
    with pytest.raises(ValueError, match="sha256"):
        R.resolve(q, rv, rh, R.Replay(s["log"]), now=R.parse_utc(s["resolved_at"]))


def _xrp_log(cg_value, kraken_close, cg_status=200, kraken_status=200):
    s = snap("ref_xrp_2026_11_25")
    cg, kr = s["log"]
    cgb = json.dumps({"market_data": {"current_price": {"usd": cg_value}}})
    kj = json.loads(kr["body"])
    key = next(k for k in kj["result"] if k != "last")
    for row in kj["result"][key]:
        if int(row[0]) == 1795478400:
            row[4] = str(kraken_close)
    return [entry("coingecko", cg["url"], cgb, cg_status), entry("kraken", kr["url"], json.dumps(kj), kraken_status)]


def test_disagreement_beyond_tolerance_is_disputed_and_not_settled():
    q, rv, rh = load("ref_xrp_2026_11_25")
    now = datetime(2026, 11, 26, tzinfo=timezone.utc)
    ok = R.resolve(q, rv, rh, R.Replay(_xrp_log(0.924, 0.926)), now=now)   # 0.22% apart: within 0.5%
    assert ok["outcome"] == "YES" and ok["confidence"] == "high" and abs(ok["raw_values"]["combined"] - 0.925) < 1e-12
    bad = R.resolve(q, rv, rh, R.Replay(_xrp_log(0.924, 0.90)), now=now)  # 2.6% apart
    assert bad["outcome"] == "DISPUTED" and bad["confidence"] == "none" and bad["raw_values"]["combined"] is None


def test_one_source_down_uses_the_other_only_if_pre_approved():
    q, rv, rh = load("ref_xrp_2026_11_25")
    now = datetime(2026, 11, 26, tzinfo=timezone.utc)
    one = R.resolve(q, rv, rh, R.Replay(_xrp_log(0.924, 0.9, cg_status=503)), now=now)
    assert one["outcome"] == "NO" and one["confidence"] == "low"
    assert [s["available"] for s in one["sources"]] == [False, True]
    # github_commits: git_protocol is NOT pre-approved to resolve alone.
    qg, rvg, rhg = load("ref_gh_2026_11_24")
    s = snap("ref_gh_2026_11_24")
    log = [e if e["source"] != "github_rest" else entry("github_rest", e["url"], '{"message":"API rate limit exceeded"}', 403) for e in s["log"]]
    log = [dict(e, headers={"x-ratelimit-remaining": "0"}) if e["source"] == "github_rest" else e for e in log][:1] + [e for e in log if e["source"] == "git_protocol"]
    r = R.resolve(qg, rvg, rhg, R.Replay(log), now=R.parse_utc(s["resolved_at"]))
    assert r["outcome"] == "UNRESOLVED" and r["confidence"] == "none"


def test_before_the_close_nothing_is_fetched_and_the_result_is_unresolved():
    q, rv, rh = load("q_2026_11_26_001")
    r = R.resolve(q, rv, rh, R.Replay([]), now=datetime(2026, 11, 27, 23, 59, tzinfo=timezone.utc))
    assert r["outcome"] == "UNRESOLVED" and r["sources"] == []


def test_reconcile_rules():
    q, rv, rh = load("ref_xrp_2026_11_25")
    now = datetime(2026, 11, 26, tzinfo=timezone.utc)
    p = R.resolve(q, rv, rh, R.Replay(_xrp_log(0.924, 0.925)), now=now)
    near = R.resolve(q, rv, rh, R.Replay(_xrp_log(0.9241, 0.9249)), now=now)
    assert R.reconcile(p, near, rv, q)["status"] == "ACCEPTED"
    far = R.resolve(q, rv, rh, R.Replay(_xrp_log(0.9285, 0.9299)), now=now)  # same outcome, kraken 0.53% from the primary
    assert R.reconcile(p, far, rv, q)["status"] == "DISPUTED"
    flipped = R.resolve(q, rv, rh, R.Replay(_xrp_log(0.91, 0.911)), now=now)
    rec = R.reconcile(p, flipped, rv, q)
    assert rec["status"] == "HUMAN_REVIEW" and rec["shown_as"] == "DISPUTED"
    assert R.reconcile(p, dict(p, resolver_hash="sha256:" + "0" * 64), rv, q)["status"] == "HUMAN_REVIEW"


def test_48h_dispute_window_and_rating_eligibility():
    pub = json.loads((FIX / "ref_xrp_2026_11_25.resolution.json").read_text())
    acc = {"status": "ACCEPTED", "shown_as": pub["outcome"]}
    t0 = R.parse_utc(pub["resolved_at"])
    assert R.rating_status(pub, acc, [], t0 + timedelta(hours=47))["state"] == "PENDING_DISPUTE_WINDOW"
    fin = R.rating_status(pub, acc, [], t0 + timedelta(hours=48))
    assert fin["state"] == "FINAL" and fin["counts_toward_rating"] is True
    d = [{"opened_at": R.utc_iso(t0 + timedelta(hours=10)), "status": "open"}]
    st = R.rating_status(pub, acc, d, t0 + timedelta(hours=100))
    assert st["state"] == "DISPUTED" and st["counts_toward_rating"] is False
    late = [{"opened_at": R.utc_iso(t0 + timedelta(hours=49)), "status": "open"}]
    assert R.rating_status(pub, acc, late, t0 + timedelta(hours=100))["state"] == "FINAL"
    rejected = [{"opened_at": R.utc_iso(t0 + timedelta(hours=10)), "status": "rejected"}]
    assert R.rating_status(pub, acc, rejected, t0 + timedelta(hours=100))["counts_toward_rating"] is True
    rev = {"status": "HUMAN_REVIEW", "shown_as": "DISPUTED"}
    assert R.rating_status(pub, rev, [], t0 + timedelta(days=9)) == {"state": "DISPUTED", "counts_toward_rating": False}


def test_who_can_open_a_dispute_and_accepted_evidence():
    assert R.can_open_dispute("agent-x", ["agent-x", "agent-y"]) is True
    assert R.can_open_dispute("agent-z", ["agent-x"]) is False
    assert R.can_open_dispute("anyone", [], is_operator=True) is True
    assert R.dispute_evidence_ok({"source_url": "https://api.kraken.com/0/public/OHLC?pair=XRPUSD", "raw_value": "0.9"})
    assert R.dispute_evidence_ok({"rule_error": "the close used D instead of D-1 candle"})
    assert not R.dispute_evidence_ok({"source_url": "http://x", "raw_value": 1})
    assert not R.dispute_evidence_ok({"note": "I disagree"})


def test_resolver_process_is_separate_from_rated_agents(tmp_path):
    doc = R.load_resolver_agents(RES)
    assert {"qants-a1", "qants-a2"} <= set(doc["rated_baselines_never_resolvers"])
    assert not set(doc["resolver_agents"]) & {"qants-a1", "qants-a2"}
    assert R.answer_flags("mizan-resolver-primary", doc) == ["resolver-conflict"]
    assert R.answer_flags("qants-a2", doc) == []
    (tmp_path / "resolver_agents.yaml").write_text("version: 1.0.0\nresolver_agents: [qants-a2]\nrated_baselines_never_resolvers: [qants-a2]\n")
    with pytest.raises(ValueError, match="rated baselines"):
        R.load_resolver_agents(tmp_path)


def test_core_library_import_does_not_pull_in_the_network_module():
    r = subprocess.run([sys.executable, "-c", "import sys, mizan_ledger; print('mizan_ledger.resolver' in sys.modules, 'urllib.request' in sys.modules)"],
                       capture_output=True, text=True, env={"PYTHONPATH": str(ROOT / "src"), "PATH": "/usr/bin:/bin"})
    assert r.stdout.split()[0] == "False"


def test_ledger_rejects_resolver_and_checker_predictions_and_excludes_from_summary(tmp_path):
    from mizan_ledger.core import Ledger, LedgerError, is_non_predicting_agent, _append_jsonl
    for bad in ("mizan-resolver-primary", "mizan-resolver-rerun", "mizan-resolver-custom", "mizan-checker", "qants-checker"):
        assert is_non_predicting_agent(bad) is True
    for ok_id in ("qants-a1", "qants-a2", "qants-blockchain-first-principles", "qants-baseline-free-llm-a", "qants-baseline-free-llm-b"):
        assert is_non_predicting_agent(ok_id) is False
    lg = Ledger(tmp_path / "pub", private_path=tmp_path / "priv")
    for bad in ("mizan-resolver-primary", "mizan-resolver-rerun", "mizan-checker", "qants-checker"):
        with pytest.raises(LedgerError, match="resolver/checker"):
            lg.declare("t-sep-1", 0.7, bad, "1.0")
    lg.declare("t-sep-ok", 0.7, "qants-a2", "1.0")
    lg.resolve("t-sep-ok", True, "qants-a2", "1.0")
    _append_jsonl(lg.receipts_path, {"schema": "mizan-v1", "seq": 2, "agent_id": "mizan-resolver-primary",
                                     "agent_version": "1.0", "commitment": "c", "declared_at": "2026-11-26T00:00:00Z", "prev": "g"})
    _append_jsonl(lg.resolutions_path, {"schema": "mizan-v1", "seq": 2, "commitment": "c",
                                        "agent_id": "mizan-resolver-primary", "agent_version": "1.0",
                                        "outcome": 1, "resolved_at": "2026-11-26T00:01:00Z",
                                        "opening": {"task_id": "t-inj", "p": "0.9000", "salt": "s"}})
    assert lg.summary("mizan-resolver-primary")["n"] == 0
    assert lg.summary("mizan-resolver-primary")["declared"] == 0
    assert lg.pairs("mizan-resolver-primary") == []
    assert lg.summary()["resolved"] == 1
