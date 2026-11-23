"""Command line: mizan-ledger verify <dir> | summary <dir> [--agent ID] | gate <dir> --baseline FILE | mcp"""
import argparse, json, sys
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
    g = sub.add_parser("gate", help="CI calibration gate: exit 0 pass, 1 worse beyond the delta, 2 too few records",
                       description="Compare the ledger's Brier score with a baseline. Exit 0 = pass, 1 = calibration got worse "
                                   "beyond --max-brier-delta and beyond the 95%% interval (or the ledger does not verify), "
                                   "2 = inconclusive (fewer than --min-n resolved records, or no baseline yet).")
    g.add_argument("dir"); g.add_argument("--baseline", default="mizan-baseline.json")
    g.add_argument("--max-brier-delta", type=float, default=0.02); g.add_argument("--min-n", type=int, default=50)
    g.add_argument("--agent"); g.add_argument("--version")
    g.add_argument("--write-baseline", action="store_true", help="create or refresh the baseline from this ledger")
    g.add_argument("--summary-md", help="also append a Markdown summary to this file (e.g. $GITHUB_STEP_SUMMARY)")
    sub.add_parser(
        "mcp",
        help="run the optional local stdio MCP server (requires the [mcp] extra)",
        description="Run the optional local stdio MCP server. Requires pip install \"mizan-ledger[mcp]\". Same entry point as the mizan-ledger-mcp script.",
    )
    a = ap.parse_args(argv)
    if a.cmd == "verify":
        rep = verify_ledger_dir(a.dir)
        print(json.dumps(rep, indent=2, sort_keys=True))
        return 0 if rep["ok"] else 1
    if a.cmd == "mcp":
        return _run_mcp()
    if a.cmd == "gate":
        from . import ci_gate
        if a.write_baseline:
            code, rep = ci_gate.write_baseline(a.dir, a.baseline, a.min_n, a.agent, a.version)
        else:
            code, rep = ci_gate.check(a.dir, a.baseline, a.max_brier_delta, a.min_n, a.agent, a.version)
        print(json.dumps(rep, indent=2, sort_keys=True))
        if a.summary_md:
            with open(a.summary_md, "a", encoding="utf-8") as fh:
                fh.write(ci_gate.markdown(code, rep))
        if code == ci_gate.INCONCLUSIVE:
            print(f"warning: {rep.get('reason')}", file=sys.stderr)
        return code
    led = Ledger(a.dir, private_path=a.dir + "/.noprivate")
    print(json.dumps(led.summary(a.agent, a.version), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
