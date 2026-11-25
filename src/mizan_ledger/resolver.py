"""Verifiable resolution for Mizan questions (hardening v1 T4).

A question names a resolver file (resolvers/<id>.yaml). The resolver fetches the value from two independent,
keyless public sources, normalises timestamps to UTC, and:
  1. both sources agree within tolerance  -> value = mean (count: the agreed count), confidence "high"
  2. they disagree beyond tolerance        -> DISPUTED (never auto-settled)
  3. one source unavailable                -> the other alone, only if it is pre-approved; confidence "low"
  4. no source / before the close          -> UNRESOLVED
Every fetch is logged: source URL, request time (UTC), HTTP status, sha256 of the raw body and the raw body itself,
so a snapshot can be replayed offline and anyone can re-fetch the same public data.

This module is the only part of mizan-ledger that touches the network, and only when `mizan resolve` runs.
No key, token, account or IP is ever sent or logged.
"""
import calendar, hashlib, json, os, re, subprocess, tempfile, urllib.error, urllib.parse, urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from . import yamlmini

RESOLVER_SCHEMA_FIELDS = ("id", "version", "category", "entity", "period", "sources", "normalization",
                          "tolerance", "missing_or_late", "outcome_rule", "reproduce")
QUESTION_FIELDS = ("id", "category", "statement", "resolution_rule", "deadline", "sources", "created_at", "question_hash")
OUTCOMES = ("YES", "NO", "DISPUTED", "UNRESOLVED")
COMPARATORS = {">": lambda v, t: v > t, ">=": lambda v, t: v >= t, "<": lambda v, t: v < t, "<=": lambda v, t: v <= t}
DISPUTE_WINDOW_HOURS = 48
USER_AGENT = "mizan-resolver/1.0"
DISCLOSURE = "Practice estimates are not ratings. Resolution is operator-assisted until decentralized adjudication is live."


def canonical(v):
    return json.dumps(v, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha256_hex(b):
    return hashlib.sha256(b if isinstance(b, bytes) else b.encode("utf-8")).hexdigest()


def utc_iso(dt):
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_utc(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(timezone.utc)


# ---------------------------------------------------------------- files, hashes

def load_resolver(resolvers_dir, rid):
    p = Path(resolvers_dir) / f"{rid}.yaml"
    raw = p.read_bytes()
    doc = yamlmini.loads(raw.decode("utf-8"))
    missing = [f for f in RESOLVER_SCHEMA_FIELDS if f not in doc]
    if missing:
        raise ValueError(f"{p.name}: missing required field(s): {', '.join(missing)}")
    if doc["id"] != rid:
        raise ValueError(f"{p.name}: id must equal the file name")
    if not isinstance(doc["sources"], list) or len(doc["sources"]) != 2 or len({s["id"] for s in doc["sources"]}) != 2:
        raise ValueError(f"{p.name}: exactly two independent sources are required")
    for s in doc["sources"]:
        if re.search(r"(api[_-]?key|token|secret)=", s.get("url", ""), re.I):
            raise ValueError(f"{p.name}: source {s['id']} must be keyless")
    return doc, "sha256:" + sha256_hex(raw)


def question_hash(q, resolver_hash):
    """sha256 of the canonical question (without question_hash) plus the resolver file's sha256."""
    body = {k: v for k, v in q.items() if k != "question_hash"}
    return "sha256:" + sha256_hex(canonical(body) + "\n" + resolver_hash)


def load_question(questions_dir, qid):
    if not re.fullmatch(r"[A-Za-z0-9_.\-]{1,80}", qid):
        raise ValueError("bad question id")
    q = json.loads((Path(questions_dir) / f"{qid}.json").read_text(encoding="utf-8"))
    missing = [f for f in QUESTION_FIELDS if f not in q]
    if missing:
        raise ValueError(f"{qid}: missing field(s): {', '.join(missing)}")
    if q["id"] != qid:
        raise ValueError(f"{qid}: id must equal the file name")
    rule = q["resolution_rule"]
    if not isinstance(rule, dict) or not {"resolver", "comparator", "threshold", "text"} <= set(rule):
        raise ValueError(f"{qid}: resolution_rule needs resolver, comparator, threshold and text")
    if rule["comparator"] not in COMPARATORS:
        raise ValueError(f"{qid}: comparator must be one of {', '.join(COMPARATORS)}")
    return q


def check_question(q, resolver, resolver_hash):
    """Integrity checks a third party runs before trusting a resolution."""
    errs = []
    if q["question_hash"] != question_hash(q, resolver_hash):
        errs.append("question_hash does not match the question and resolver file (edited after publication?)")
    if q["resolution_rule"].get("resolver_hash") not in (None, resolver_hash):
        errs.append("resolution_rule.resolver_hash does not match the resolver file")
    if sorted(q["sources"]) != sorted(s["id"] for s in resolver["sources"]):
        errs.append("question sources differ from the resolver's sources")
    if q["category"] != resolver["category"]:
        errs.append("question category differs from the resolver's category")
    if parse_utc(q["created_at"]) >= parse_utc(q["deadline"]):
        errs.append("created_at must be before the deadline (the rule is published before the deadline)")
    return errs


# ---------------------------------------------------------------- fetching with a replayable log

class Fetcher:
    """Live HTTP fetcher that logs everything needed to replay the fetch."""

    def __init__(self, timeout=20, now=None):
        self.timeout, self.log, self._now = timeout, [], now

    def now(self):
        return self._now() if self._now else datetime.now(timezone.utc)

    def get(self, source_id, url, headers=None):
        t = utc_iso(self.now())
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json", **(headers or {})})
        status, body, hdr = None, b"", {}
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                status, body = r.status, r.read()
                hdr = {k.lower(): v for k, v in r.headers.items() if k.lower().startswith("x-ratelimit")}
        except urllib.error.HTTPError as e:
            status, body = e.code, e.read() or b""
            hdr = {k.lower(): v for k, v in e.headers.items() if k.lower().startswith("x-ratelimit")}
        except Exception as e:  # network down, DNS, timeout: the source is unavailable
            status, body = None, ("error: " + type(e).__name__).encode()
        entry = {"source": source_id, "url": url, "request_time": t, "status": status, "headers": hdr,
                 "body_sha256": "sha256:" + sha256_hex(body), "body": body.decode("utf-8", "replace")}
        self.log.append(entry)
        return entry

    def git_count(self, source_id, url, since, until):
        t = utc_iso(self.now())
        out = {"source": source_id, "url": url, "request_time": t, "status": None, "headers": {}}
        with tempfile.TemporaryDirectory() as d:
            env = dict(os.environ, GIT_TERMINAL_PROMPT="0", GIT_ASKPASS="/bin/false")
            c = subprocess.run(["git", "-c", "credential.helper=", "clone", "-q", "--bare", "--filter=blob:none", url, d + "/r"],
                               capture_output=True, text=True, env=env, timeout=300)
            if c.returncode == 0:
                r = subprocess.run(["git", "-C", d + "/r", "rev-list", "--count", f"--since={since}", f"--until={until}", "HEAD"],
                                   capture_output=True, text=True, timeout=120)
                if r.returncode == 0:
                    out["status"], body = 200, r.stdout.strip()
                else:
                    body = "error: rev-list failed"
            else:
                body = "error: clone failed"
        out["body_sha256"] = "sha256:" + sha256_hex(body)
        out["body"] = body
        self.log.append(out)
        return out


class Replay(Fetcher):
    """Replays a recorded log offline: same URLs in the same order, byte-identical bodies."""

    def __init__(self, entries):
        super().__init__()
        self._entries = list(entries)

    def _next(self, source_id, url):
        for i, e in enumerate(self._entries):
            if e["source"] == source_id and e["url"] == url:
                e = self._entries.pop(i)
                if "body_sha256" in e and e["body_sha256"] != "sha256:" + sha256_hex(e["body"]):
                    raise ValueError(f"snapshot body for {url} does not match its sha256")
                self.log.append(e)
                return e
        raise LookupError(f"snapshot has no recorded response for {source_id} {url}")

    def get(self, source_id, url, headers=None):
        return self._next(source_id, url)

    def git_count(self, source_id, url, since, until):
        return self._next(source_id, url)


# ---------------------------------------------------------------- source adapters (value or None)

def _ny_close_utc(d):
    from zoneinfo import ZoneInfo
    return datetime(d.year, d.month, d.day, 16, 0, tzinfo=ZoneInfo("America/New_York")).astimezone(timezone.utc)


def _json(entry):
    if entry["status"] != 200:
        return None
    try:
        return json.loads(entry["body"])
    except ValueError:
        return None


def src_coingecko(f, q, d):
    e = f.get("coingecko", f"https://api.coingecko.com/api/v3/coins/ripple/history?date={d:%d-%m-%Y}&localization=false")
    j = _json(e)
    try:
        return float(j["market_data"]["current_price"]["usd"]), e
    except (TypeError, KeyError, ValueError):
        return None, e


def src_kraken(f, q, d):
    start = calendar.timegm((d - timedelta(days=1)).timetuple())
    e = f.get("kraken", f"https://api.kraken.com/0/public/OHLC?pair=XRPUSD&interval=1440&since={start - 1}")
    j = _json(e)
    try:
        if j.get("error"):
            return None, e
        rows = next(iter(j["result"][k] for k in j["result"] if k != "last"))
        for row in rows:
            # A candle is final only once the next one has opened: Kraken's last row is the running candle.
            if int(row[0]) == start and row is not rows[-1]:
                return float(row[4]), e
    except (TypeError, KeyError, ValueError, StopIteration):
        pass
    return None, e


def src_yahoo(f, q, d):
    sym = q["resolution_rule"]["symbol"]
    p1 = calendar.timegm(d.timetuple())
    e = f.get("yahoo", f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}?interval=1d&period1={p1}&period2={p1 + 86400}")
    j = _json(e)
    try:
        res = j["chart"]["result"][0]
        off = int(res["meta"]["gmtoffset"])
        for i, ts in enumerate(res["timestamp"]):
            if datetime.fromtimestamp(ts + off, timezone.utc).date() == d:
                c = res["indicators"]["quote"][0]["close"][i]
                return (float(c) if c is not None else None), e
    except (TypeError, KeyError, ValueError, IndexError):
        pass
    return None, e


def src_nasdaq(f, q, d):
    sym = q["resolution_rule"]["symbol"]
    e = f.get("nasdaq", f"https://api.nasdaq.com/api/quote/{sym}/historical?assetclass=etf&fromdate={d:%Y-%m-%d}&todate={d + timedelta(days=1):%Y-%m-%d}&limit=5")
    j = _json(e)
    try:
        for row in j["data"]["tradesTable"]["rows"]:
            if row["date"] == f"{d:%m/%d/%Y}":
                return float(row["close"].replace("$", "").replace(",", "")), e
    except (TypeError, KeyError, ValueError):
        pass
    return None, e


def src_github_rest(f, q, d):
    rule = q["resolution_rule"]
    total, page, last = 0, 1, None
    while True:
        qs = urllib.parse.urlencode({"since": rule["window_start"], "until": rule["window_end"], "per_page": 100, "page": page})
        e = f.get("github_rest", f"https://api.github.com/repos/{rule['repository']}/commits?{qs}",
                  headers={"Accept": "application/vnd.github+json"})
        last = e
        if e["headers"].get("x-ratelimit-remaining") == "0" and e["status"] != 200:
            return None, e
        j = _json(e)
        if not isinstance(j, list):
            return None, e
        total += len(j)
        if len(j) < 100:
            return float(total), last
        page += 1
        if page > 50:
            return None, e


def src_git_protocol(f, q, d):
    rule = q["resolution_rule"]
    e = f.git_count("git_protocol", f"https://github.com/{rule['repository']}.git", rule["window_start"], rule["window_end"])
    try:
        return (float(int(e["body"])) if e["status"] == 200 else None), e
    except ValueError:
        return None, e


ADAPTERS = {"coingecko": src_coingecko, "kraken": src_kraken, "yahoo": src_yahoo, "nasdaq": src_nasdaq,
            "github_rest": src_github_rest, "git_protocol": src_git_protocol}


# ---------------------------------------------------------------- the rule

def value_date(q, resolver):
    if resolver["id"] == "github_commits":
        return parse_utc(q["resolution_rule"]["window_end"]).date()
    return date.fromisoformat(q["resolution_rule"]["date"])


def close_utc(q, resolver):
    if resolver["id"] == "spy_daily_close":
        return _ny_close_utc(value_date(q, resolver))
    if resolver["id"] == "github_commits":
        return parse_utc(q["resolution_rule"]["window_end"])
    d = value_date(q, resolver)
    return datetime(d.year, d.month, d.day, tzinfo=timezone.utc)


def within_tolerance(a, b, resolver, q):
    tol = resolver["tolerance"]
    if "tolerance_pct" in q["resolution_rule"]:
        tol = {"relative_pct": q["resolution_rule"]["tolerance_pct"]}
    if "absolute" in tol:
        return abs(a - b) <= float(tol["absolute"])
    m = (a + b) / 2
    return m != 0 and abs(a - b) / abs(m) * 100 <= float(tol["relative_pct"])


def resolve(q, resolver, resolver_hash, fetcher, now=None):
    now = now or fetcher.now()
    rule = q["resolution_rule"]
    sources, raw, out = [], {}, {"question_id": q["id"]}
    if resolver["id"] == "spy_daily_close" and rule.get("symbol") not in resolver["entity"]["allowed_symbols"]:
        raise ValueError("symbol not allowed by this resolver")
    wait = timedelta(minutes=int(resolver["missing_or_late"]["wait_after_close_minutes"]))
    if now < close_utc(q, resolver) + wait:
        outcome, conf, value = "UNRESOLVED", "none", None
    else:
        d = value_date(q, resolver)
        vals = {}
        for s in resolver["sources"]:
            v, e = ADAPTERS[s["id"]](fetcher, q, d)
            vals[s["id"]] = v
            raw[s["id"]] = v
            sources.append({"id": s["id"], "url": e["url"], "request_time": e["request_time"], "status": e["status"],
                            "body_sha256": e["body_sha256"], "available": v is not None,
                            "fallback_pre_approved": bool(s.get("fallback_pre_approved"))})
        got = [s for s in resolver["sources"] if vals[s["id"]] is not None]
        if len(got) == 2:
            a, b = (vals[s["id"]] for s in resolver["sources"])
            if within_tolerance(a, b, resolver, q):
                value, conf = (a + b) / 2, "high"
            else:
                value, conf = None, "none"
        elif len(got) == 1 and got[0].get("fallback_pre_approved"):
            value, conf = vals[got[0]["id"]], "low"
        else:
            value, conf = None, "none"
        if value is not None:
            outcome = "YES" if COMPARATORS[rule["comparator"]](value, float(rule["threshold"])) else "NO"
        elif len(got) == 2:
            outcome = "DISPUTED"
        else:
            outcome = "UNRESOLVED"
        raw["combined"] = value
    raw["close_utc"] = utc_iso(close_utc(q, resolver))
    out.update({"outcome": outcome, "confidence": conf, "sources": sources, "raw_values": raw,
                "resolved_at": utc_iso(now), "resolver_version": resolver["version"], "resolver_hash": resolver_hash})
    return out


# ---------------------------------------------------------------- re-run, dispute window, rating eligibility

def reconcile(primary, rerun, resolver, q):
    """Primary resolution vs the independent re-run (founder rules, 26 Nov 2026).

    Same outcome and every raw value within tolerance -> ACCEPTED.
    Same outcome but a raw value beyond tolerance       -> DISPUTED.
    Different outcome                                    -> HUMAN_REVIEW (shown as DISPUTED; never auto-accepted).
    """
    if primary["question_id"] != rerun["question_id"] or primary["resolver_hash"] != rerun["resolver_hash"]:
        return {"status": "HUMAN_REVIEW", "shown_as": "DISPUTED", "reason": "the two runs did not use the same question or resolver file"}
    if primary["outcome"] != rerun["outcome"]:
        return {"status": "HUMAN_REVIEW", "shown_as": "DISPUTED", "reason": f"outcome {primary['outcome']} vs re-run {rerun['outcome']}"}
    if primary["outcome"] in ("DISPUTED", "UNRESOLVED"):
        return {"status": primary["outcome"], "shown_as": primary["outcome"], "reason": "no settled outcome to accept"}
    for k in (s["id"] for s in resolver["sources"]):
        a, b = primary["raw_values"].get(k), rerun["raw_values"].get(k)
        if (a is None) != (b is None) or (a is not None and not within_tolerance(a, b, resolver, q)):
            return {"status": "DISPUTED", "shown_as": "DISPUTED", "reason": f"source {k}: {a} vs re-run {b}"}
    return {"status": "ACCEPTED", "shown_as": primary["outcome"], "reason": "re-run matches within tolerance"}


def rating_status(resolution, reconciliation, disputes, now):
    """Where a resolved question stands for ratings.

    disputes: list of {"opened_at", "status": "open"|"upheld"|"rejected", ...}. Answers are never voided: while a
    question is pending or disputed its answers stay visible but do not enter Brier or any rating.
    """
    if reconciliation["status"] != "ACCEPTED":
        return {"state": reconciliation["shown_as"] if reconciliation["shown_as"] in ("DISPUTED", "UNRESOLVED") else "DISPUTED",
                "counts_toward_rating": False}
    closes = parse_utc(resolution["resolved_at"]) + timedelta(hours=DISPUTE_WINDOW_HOURS)
    in_window = [x for x in disputes if parse_utc(x["opened_at"]) < closes]
    if any(x["status"] in ("open", "upheld") for x in in_window):
        return {"state": "DISPUTED", "counts_toward_rating": False, "dispute_window_closes_at": utc_iso(closes)}
    if now < closes:
        return {"state": "PENDING_DISPUTE_WINDOW", "counts_toward_rating": False, "dispute_window_closes_at": utc_iso(closes)}
    return {"state": "FINAL", "counts_toward_rating": True, "dispute_window_closes_at": utc_iso(closes)}


def can_open_dispute(agent_id, answered_agent_ids, is_operator=False):
    """Who may open a dispute: any agent (key holder) that answered the question, and the operator."""
    return bool(is_operator or agent_id in set(answered_agent_ids))


def dispute_evidence_ok(evidence):
    """Accepted evidence: a public source URL with the raw value it shows, or a named rule-application error."""
    if not isinstance(evidence, dict):
        return False
    url_ok = isinstance(evidence.get("source_url"), str) and evidence["source_url"].startswith("https://") and "raw_value" in evidence
    rule_ok = isinstance(evidence.get("rule_error"), str) and len(evidence["rule_error"].strip()) >= 10
    return url_ok or rule_ok


def load_resolver_agents(resolvers_dir):
    doc = yamlmini.loads((Path(resolvers_dir) / "resolver_agents.yaml").read_text(encoding="utf-8"))
    overlap = set(doc["resolver_agents"]) & set(doc.get("rated_baselines_never_resolvers", []))
    if overlap:
        raise ValueError(f"resolver_agents.yaml: rated baselines listed as resolvers: {sorted(overlap)}")
    return doc


def answer_flags(agent_id, resolver_agents_doc):
    """'resolver-conflict' when a resolver identity shows up as an answering agent: excluded from Brier and ratings."""
    return ["resolver-conflict"] if agent_id in set(resolver_agents_doc["resolver_agents"]) else []


# ---------------------------------------------------------------- reproduce entry point

def reproduce(question_id, questions_dir="questions", resolvers_dir="resolvers", snapshot=None, now=None, log_out=None):
    q = load_question(questions_dir, question_id)
    resolver, rh = load_resolver(resolvers_dir, q["resolution_rule"]["resolver"])
    errs = check_question(q, resolver, rh)
    if errs:
        raise ValueError("; ".join(errs))
    if snapshot is not None:
        snap = json.loads(Path(snapshot).read_text(encoding="utf-8"))
        f = Replay(snap["log"])
        now = now or parse_utc(snap["resolved_at"])
    else:
        f = Fetcher()
    res = resolve(q, resolver, rh, f, now=now)
    if log_out:
        Path(log_out).write_text(json.dumps({"question_id": q["id"], "question_hash": q["question_hash"], "resolved_at": res["resolved_at"],
                                             "resolver_hash": rh, "log": f.log}, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    return res
