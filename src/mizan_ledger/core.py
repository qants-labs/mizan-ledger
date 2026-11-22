"""Core ledger: salted commitments, hash chain, resolution, offline verification.

Design rules
  * The public receipt contains only a salted hash, the agent id/version and a timestamp.
  * The opening (task_id, p, salt) stays in a private file until the task is resolved.
  * Timestamps come from the local clock. They are self-reported until the chain head
    hash is anchored somewhere independent (see README, "Anchoring").
"""
from __future__ import annotations
import datetime as _dt
import hashlib, json, math, os, secrets
from pathlib import Path
from typing import Callable, Optional

from . import metrics

SCHEMA = "mizan-ledger/1"
GENESIS = "0" * 64
LOG_LOSS_CLIP = metrics.LOG_LOSS_CLIP


class LedgerError(Exception):
    pass


def canonical_json(obj) -> str:
    """Sorted keys, no whitespace, UTF-8 characters kept (matches JSON.stringify on sorted keys)."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha256_hex(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def fmt_p(p: float) -> str:
    """Fixed 6-decimal string so every language serialises the same probability identically."""
    p = float(p)
    if not (0.0 <= p <= 1.0) or math.isnan(p):
        raise LedgerError("p_success must be a number in [0, 1]")
    return f"{p:.6f}"


def commitment_hash(task_id: str, p: float, agent_id: str, version: str, salt: str) -> str:
    return sha256_hex(canonical_json({
        "agent_id": agent_id, "agent_version": version, "p": fmt_p(p),
        "salt": salt, "schema": SCHEMA, "task_id": task_id,
    }))


def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _parse(ts: str) -> _dt.datetime:
    return _dt.datetime.fromisoformat(ts.replace("Z", "+00:00"))


def _chain_hash(rec: dict, key: str) -> str:
    body = {k: v for k, v in rec.items() if k != key}
    return sha256_hex(canonical_json(body))


def _read_jsonl(path: Path) -> list:
    if not path.exists():
        return []
    out = []
    with path.open("r", encoding="utf-8") as f:
        for i, line in enumerate(f, 1):
            line = line.strip()
            if line:
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError as e:
                    raise LedgerError(f"{path.name}: line {i} is not valid JSON ({e})")
    return out


def _append_jsonl(path: Path, rec: dict, private: bool = False) -> None:
    new = not path.exists()
    with path.open("a", encoding="utf-8") as f:
        f.write(canonical_json(rec) + "\n")
        f.flush(); os.fsync(f.fileno())
    if new and private:
        os.chmod(path, 0o600)


def _check_evidence(ev):
    """Optional resolver evidence: a flat JSON object of strings/numbers/bools, sealed into the record hash."""
    if ev is None:
        return None
    if not isinstance(ev, dict) or not ev:
        raise LedgerError("evidence must be a non-empty object")
    for k, v in ev.items():
        if not isinstance(k, str) or not (isinstance(v, (str, int, float, bool)) or v is None):
            raise LedgerError("evidence must map strings to strings, numbers, booleans or null")
        if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
            raise LedgerError("evidence numbers must be finite")
    return json.loads(canonical_json(ev))


class Ledger:
    """Append-only local ledger.

    public dir:   receipts.jsonl, resolutions.jsonl   (safe to share; receipts are hash-only)
                  a resolution has outcome 1, 0 or "void" (never scored), and optional sealed resolver evidence
    private dir:  openings.jsonl                      (task_id, p, salt; keep secret until resolved)
    """

    def __init__(self, path, private_path=None, clock: Optional[Callable[[], str]] = None):
        self.dir = Path(path)
        self.private_dir = Path(private_path) if private_path else self.dir / "private"
        self.dir.mkdir(parents=True, exist_ok=True)
        self._clock = clock or _now
        self.receipts_path = self.dir / "receipts.jsonl"
        self.resolutions_path = self.dir / "resolutions.jsonl"
        self.openings_path = self.private_dir / "openings.jsonl"

    # -- reading ---------------------------------------------------------------
    def receipts(self) -> list: return _read_jsonl(self.receipts_path)
    def resolutions(self) -> list: return _read_jsonl(self.resolutions_path)
    def _openings(self) -> list: return _read_jsonl(self.openings_path)

    def _find_opening(self, task_id, agent_id, version):
        for o in self._openings():
            if o["task_id"] == task_id and o["agent_id"] == agent_id and o["agent_version"] == version:
                return o
        return None

    # -- declare ---------------------------------------------------------------
    def declare(self, task_id: str, p_success: float, agent_id: str, version: str = "unspecified") -> dict:
        """Commit to a probability of success BEFORE the outcome is known. Returns the public receipt."""
        if not task_id or not agent_id:
            raise LedgerError("task_id and agent_id are required")
        p = float(p_success)
        fmt_p(p)  # validates
        if self._find_opening(task_id, agent_id, version):
            raise LedgerError(f"task {task_id!r} was already declared for {agent_id}@{version}; a declaration cannot be replaced")
        self.private_dir.mkdir(parents=True, exist_ok=True)
        try: os.chmod(self.private_dir, 0o700)
        except OSError: pass
        salt = secrets.token_hex(16)
        commitment = commitment_hash(task_id, p, agent_id, version, salt)
        recs = self.receipts()
        prev = recs[-1]["receipt_hash"] if recs else GENESIS
        rec = {"schema": SCHEMA, "seq": len(recs) + 1, "agent_id": agent_id, "agent_version": version,
               "commitment": commitment, "declared_at": self._clock(), "prev": prev}
        rec["receipt_hash"] = _chain_hash(rec, "receipt_hash")
        # private opening first: if the process dies between the two writes we never publish a hash we cannot open
        _append_jsonl(self.openings_path, {"agent_id": agent_id, "agent_version": version, "task_id": task_id,
                                           "p": fmt_p(p), "salt": salt, "commitment": commitment}, private=True)
        _append_jsonl(self.receipts_path, rec)
        return rec

    # -- resolve ---------------------------------------------------------------
    def _open_for(self, task_id, agent_id, version):
        cands = [o for o in self._openings() if o["task_id"] == task_id
                 and (agent_id is None or o["agent_id"] == agent_id)
                 and (version is None or o["agent_version"] == version)]
        if not cands:
            raise LedgerError(f"no declaration found for task {task_id!r}")
        if len(cands) > 1:
            raise LedgerError("task_id is ambiguous; pass agent_id and version")
        return cands[0]

    def _append_resolution(self, o: dict, body: dict) -> dict:
        res = self.resolutions()
        if any(r["commitment"] == o["commitment"] for r in res):
            raise LedgerError(f"task {o['task_id']!r} is already resolved; outcomes cannot be changed")
        rcpt = next(r for r in self.receipts() if r["commitment"] == o["commitment"])
        now = self._clock()
        if _parse(now) < _parse(rcpt["declared_at"]):
            raise LedgerError("resolution time is earlier than declaration time (clock moved backwards)")
        rec = {"schema": SCHEMA, "seq": len(res) + 1, "commitment": o["commitment"],
               "agent_id": o["agent_id"], "agent_version": o["agent_version"], "resolved_at": now,
               "opening": {"task_id": o["task_id"], "p": o["p"], "salt": o["salt"]},
               "prev": res[-1]["record_hash"] if res else GENESIS}
        rec.update(body)
        rec["record_hash"] = _chain_hash(rec, "record_hash")
        _append_jsonl(self.resolutions_path, rec)
        return rec

    def resolve(self, task_id: str, outcome: bool, agent_id: Optional[str] = None, version: Optional[str] = None,
                evidence: Optional[dict] = None) -> dict:
        """Record the real outcome (True = the task succeeded). Reveals the opening so anyone can verify it.

        evidence (optional): what the resolver read, e.g. {"source": url, "value_read": "1.0992",
        "read_at": iso, "raw_sha256": hex}. It is sealed into the record hash, so editing it later breaks verify.
        """
        o = self._open_for(task_id, agent_id, version)
        y = 1 if outcome else 0
        p = float(o["p"])
        q = min(LOG_LOSS_CLIP[1], max(LOG_LOSS_CLIP[0], p))
        body = {"outcome": y,
                "score": {"squared_error": round((p - y) ** 2, 12),
                          "log_loss": round(-(math.log(q) if y else math.log(1 - q)), 12)}}
        ev = _check_evidence(evidence)
        if ev is not None: body["evidence"] = ev
        return self._append_resolution(o, body)

    def void(self, task_id: str, reason: str, agent_id: Optional[str] = None, version: Optional[str] = None,
             evidence: Optional[dict] = None) -> dict:
        """Close a declaration with no scorable outcome (e.g. the source was not read inside its window).

        A void is final, reveals the opening like any resolution, and is never scored. It still counts
        against coverage, so voiding cannot be used to hide wrong answers.
        """
        if not isinstance(reason, str) or not reason.strip():
            raise LedgerError("a void needs a non-empty reason")
        o = self._open_for(task_id, agent_id, version)
        body = {"outcome": "void", "void_reason": reason.strip()}
        ev = _check_evidence(evidence)
        if ev is not None: body["evidence"] = ev
        return self._append_resolution(o, body)

    # -- scoring ---------------------------------------------------------------
    def pairs(self, agent_id=None, version=None) -> list:
        return [(float(r["opening"]["p"]), r["outcome"]) for r in self.resolutions() if r["outcome"] in (0, 1)
                and (agent_id is None or r["agent_id"] == agent_id) and (version is None or r["agent_version"] == version)]

    def summary(self, agent_id=None, version=None) -> dict:
        rcpts = [r for r in self.receipts() if (agent_id is None or r["agent_id"] == agent_id)
                 and (version is None or r["agent_version"] == version)]
        pr = self.pairs(agent_id, version)
        nv = sum(1 for r in self.resolutions() if r["outcome"] == "void"
                 and (agent_id is None or r["agent_id"] == agent_id) and (version is None or r["agent_version"] == version))
        out = metrics.summarize(pr) if pr else {"n": 0}
        out.update({"declared": len(rcpts), "resolved": len(pr), "voided": nv, "pending": len(rcpts) - len(pr) - nv,
                    "coverage": (len(pr) / len(rcpts)) if rcpts else None})
        return out

    def chain_head(self) -> dict:
        r = self.receipts(); s = self.resolutions()
        return {"receipts": len(r), "receipt_head": r[-1]["receipt_hash"] if r else GENESIS,
                "resolutions": len(s), "resolution_head": s[-1]["record_hash"] if s else GENESIS}


# ---------------------------------------------------------------------------
# Offline verification (needs no network and no private files)
# ---------------------------------------------------------------------------
def verify_opening(receipt: dict, opening: dict) -> bool:
    """Does (task_id, p, salt) hash to the receipt's commitment?"""
    try:
        return commitment_hash(opening["task_id"], float(opening["p"]), receipt["agent_id"],
                               receipt["agent_version"], opening["salt"]) == receipt["commitment"]
    except (KeyError, ValueError, LedgerError):
        return False


def verify_chain(records: list, hash_key: str) -> list:
    errs, prev = [], GENESIS
    for i, r in enumerate(records, 1):
        if r.get("seq") != i: errs.append(f"{hash_key}: seq {r.get('seq')} at position {i}")
        if r.get("prev") != prev: errs.append(f"{hash_key}: broken link at seq {i}")
        if r.get(hash_key) != _chain_hash(r, hash_key): errs.append(f"{hash_key}: hash mismatch at seq {i}")
        prev = r.get(hash_key)
    return errs


def verify_ledger_dir(path) -> dict:
    """Verify receipts.jsonl and resolutions.jsonl in a directory. Returns {ok, errors, summary}."""
    d = Path(path)
    errs = []
    try:
        receipts = _read_jsonl(d / "receipts.jsonl")
        resolutions = _read_jsonl(d / "resolutions.jsonl")
    except LedgerError as e:
        return {"ok": False, "errors": [str(e)], "summary": None}
    errs += verify_chain(receipts, "receipt_hash")
    errs += verify_chain(resolutions, "record_hash")
    by_c = {r["commitment"]: r for r in receipts}
    seen = set()
    for r in resolutions:
        rc = by_c.get(r.get("commitment"))
        if not rc: errs.append(f"resolution seq {r.get('seq')}: no matching receipt"); continue
        if r["commitment"] in seen: errs.append(f"resolution seq {r['seq']}: duplicate resolution")
        seen.add(r["commitment"])
        if rc["agent_id"] != r.get("agent_id") or rc["agent_version"] != r.get("agent_version"):
            errs.append(f"resolution seq {r['seq']}: agent/version differs from receipt")
        if not verify_opening(rc, r.get("opening", {})): errs.append(f"resolution seq {r['seq']}: opening does not match commitment")
        try:
            if _parse(r["resolved_at"]) < _parse(rc["declared_at"]): errs.append(f"resolution seq {r['seq']}: resolved before declared")
        except Exception: errs.append(f"resolution seq {r['seq']}: bad timestamp")
        if "evidence" in r:
            try: _check_evidence(r["evidence"])
            except LedgerError as e: errs.append(f"resolution seq {r['seq']}: {e}")
        if r.get("outcome") == "void":
            if not isinstance(r.get("void_reason"), str) or not r["void_reason"].strip():
                errs.append(f"resolution seq {r['seq']}: void without a reason")
            if "score" in r: errs.append(f"resolution seq {r['seq']}: a void must not carry a score")
            continue
        try:
            p = float(r["opening"]["p"]); y = r["outcome"]
            if y not in (0, 1) or isinstance(y, bool): errs.append(f"resolution seq {r['seq']}: outcome must be 0, 1 or \"void\"")
            elif abs(r["score"]["squared_error"] - (p - y) ** 2) > 1e-9: errs.append(f"resolution seq {r['seq']}: score does not match p and outcome")
        except Exception: errs.append(f"resolution seq {r['seq']}: malformed record")
    pairs = [(float(r["opening"]["p"]), r["outcome"]) for r in resolutions if r.get("outcome") in (0, 1)]
    summ = metrics.summarize(pairs) if pairs else {"n": 0}
    nv = sum(1 for r in resolutions if r.get("outcome") == "void")
    summ.update({"declared": len(receipts), "resolved": len(pairs), "voided": nv,
                 "pending": len(receipts) - len(pairs) - nv})
    return {"ok": not errs, "errors": errs, "summary": summ,
            "chain_heads": {"receipt_head": receipts[-1]["receipt_hash"] if receipts else GENESIS,
                            "resolution_head": resolutions[-1]["record_hash"] if resolutions else GENESIS}}
