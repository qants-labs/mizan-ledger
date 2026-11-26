"""Command line: mizan-ledger verify <dir> | summary <dir> [--agent ID] | gate <dir> --baseline FILE | resolve --question-id ID --reproduce | mcp

Also installed as `mizan` (same commands): mizan resolve --question-id q_2026_11_26_001 --reproduce
"""
import argparse, json, os, sys
from .core import Ledger, verify_ledger_dir

_MCP_HINT = 'The MCP extra is not installed. Run: pip install "mizan-ledger[mcp]"'


def _run_mcp() -> int:
    try:
        from .mcp_server import main as mcp_main
    except SystemExit as exc:
        # mcp_server raises SystemExit(msg) on import when the extra is missing.
        msg = exc.code if isinstance(getattr(exc, "code", None), str) else _MCP_HINT
        print(msg, file=sys.stderr)
        return 1
    except ImportError:
        print(_MCP_HINT, file=sys.stderr)
        return 1
    mcp_main()
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="mizan-ledger", description="Offline verification and scoring of a local confidence ledger")
    sub = ap.add_subparsers(dest="cmd", required=True)
    v = sub.add_parser("verify", help="verify hash chains, openings and scores (no network, no private files)"); v.add_argument("dir")
    s = sub.add_parser("summary", help="print scores for a ledger directory"); s.add_argument("dir"); s.add_argument("--agent"); s.add_argument("--version")
    g = sub.add_parser("gate", help="CI calibration gate (Mizan Gate): exit 0 pass, 1 regression, 2 only with --strict",
                       description="Compare this run's calibration with a baseline. Input: a ledger directory or a JSONL "
                                   "file ({\"id\", \"confidence\": 0..1 or null, \"correct\"} per line). Exit 1 when Brier "
                                   "or the overconfident-wrong share got worse beyond its threshold even at the best end of "
                                   "the 95%% interval, or the input does not verify. Below --min-n answered items: no score, "
                                   "exit 0. No baseline: it is created, exit 0 (exit 2 with --strict). Thresholds are QANTS "
                                   "starting values, not industry standards.")
    g.add_argument("dir", metavar="input", help="ledger directory or JSONL file")
    g.add_argument("--baseline", default="mizan-baseline.json")
    g.add_argument("--max-brier-delta", type=float, default=0.02)
    g.add_argument("--max-ocw-delta", type=float, default=0.02, help="allowed rise of the overconfident-wrong share")
    g.add_argument("--overconfident-at", type=float, default=0.9, help="confidence at or above which a wrong answer counts as overconfident")
    g.add_argument("--min-n", type=int, default=50)
    g.add_argument("--agent"); g.add_argument("--version")
    g.add_argument("--strict", action="store_true", help="exit 2 when there is no baseline yet (it is still created)")
    g.add_argument("--write-baseline", action="store_true", help="create or refresh the baseline from this input")
    g.add_argument("--summary-md", help="also append a Markdown summary to this file (e.g. $GITHUB_STEP_SUMMARY)")
    sub.add_parser(
        "mcp",
        help="run the optional local stdio MCP server (requires the [mcp] extra)",
        description="Run the optional local stdio MCP server. Requires pip install \"mizan-ledger[mcp]\". Same entry point as the mizan-ledger-mcp script.",
    )
    r = sub.add_parser("resolve", help="resolve or reproduce a Mizan question from public data (network: only this command)",
                       description="Resolve a question from its published resolver file and two keyless public sources. "
                                   "--reproduce re-runs the published rule; with --snapshot it replays a recorded fetch log "
                                   "offline (byte-identical inputs). " + "Practice estimates are not ratings. Resolution is "
                                   "operator-assisted until decentralized adjudication is live.")
    r.add_argument("--question-id", required=True)
    r.add_argument("--reproduce", action="store_true", help="re-run the published resolution rule and print the result")
    r.add_argument("--questions", default="questions", help="folder with <question-id>.json (default: ./questions)")
    r.add_argument("--resolvers", default="resolvers", help="folder with the resolver YAML files (default: ./resolvers)")
    r.add_argument("--snapshot", help="replay this recorded fetch log instead of fetching live")
    r.add_argument("--log", help="write the full fetch log (URLs, request times, raw bodies, sha256) to this file")
    r.add_argument("--compare", help="a published resolution JSON: also print the re-run comparison (exit 1 unless ACCEPTED)")
    a = ap.parse_args(argv)
    if a.cmd == "resolve":
        from . import resolver as R
        try:
            res = R.reproduce(a.question_id, a.questions, a.resolvers, snapshot=a.snapshot, log_out=a.log)
        except (ValueError, LookupError, OSError) as exc:
            print(json.dumps({"question_id": a.question_id, "error": str(exc)}), file=sys.stderr)
            return 2
        print(json.dumps(res, indent=2, sort_keys=True))
        if a.compare:
            q = R.load_question(a.questions, a.question_id)
            rv, _ = R.load_resolver(a.resolvers, q["resolution_rule"]["resolver"])
            with open(a.compare, encoding="utf-8") as fh:
                published = json.load(fh)
            rec = R.reconcile(published, res, rv, q)
            print(json.dumps({"reconciliation": rec}, indent=2, sort_keys=True), file=sys.stderr)
            return 0 if rec["status"] == "ACCEPTED" else 1
        return 0
    if a.cmd == "verify":
        rep = verify_ledger_dir(a.dir)
        print(json.dumps(rep, indent=2, sort_keys=True))
        return 0 if rep["ok"] else 1
    if a.cmd == "mcp":
        return _run_mcp()
    if a.cmd == "gate":
        from . import ci_gate
        if a.write_baseline:
            code, rep = ci_gate.write_baseline(a.dir, a.baseline, a.min_n, a.agent, a.version, a.overconfident_at)
        else:
            code, rep = ci_gate.check(a.dir, a.baseline, a.max_brier_delta, a.min_n, a.agent, a.version,
                                      a.max_ocw_delta, a.overconfident_at, a.strict)
        print(json.dumps(rep, indent=2, sort_keys=True))
        if a.summary_md:
            with open(a.summary_md, "a", encoding="utf-8") as fh:
                fh.write(ci_gate.markdown(code, rep))
        if rep.get("result") in ("not enough data", "inconclusive: baseline created"):
            print(f"note: {rep.get('reason')}", file=sys.stderr)
        return code
    led = Ledger(a.dir, private_path=a.dir + "/.noprivate")
    print(json.dumps(led.summary(a.agent, a.version), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
