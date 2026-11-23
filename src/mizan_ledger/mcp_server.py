"""Local stdio MCP server for mizan-ledger (optional extra: pip install "mizan-ledger[mcp]").

Five tools wrap the offline Ledger: commit_confidence, resolve_outcome,
get_calibration, verify_ledger, should_act.

Safety contract
  * stdio only. No network calls, no telemetry, no API keys, no secrets.
  * No shell or subprocess execution, no financial actions.
  * Writes only append-only ledger files under MIZAN_LEDGER_DIR / MIZAN_PRIVATE_DIR
    (commit_confidence, resolve_outcome, and should_act only when commit=true). get_calibration
    and verify_ledger are read-only and never create directories.
  * Scores stated confidence against an outcome the caller supplies. It cannot verify
    that a claimed success is true.

mcp-name: io.github.qants-labs/mizan-ledger
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Optional

try:
    from mcp.server import MCPServer
    from mcp.server.mcpserver.exceptions import ToolError
    from mcp.types import ToolAnnotations
except ImportError as exc:  # pragma: no cover - only without the extra
    raise SystemExit('The MCP extra is not installed. Run: pip install "mizan-ledger[mcp]"') from exc

from . import __version__
from .core import Ledger, LedgerError, verify_ledger_dir
from .gate import should_act as _should_act

class LedgerToolError(ToolError, ValueError):
    """Expected input/ledger error: its message is returned to the client as a tool error."""


OUTCOME_WARNING = ("mizan-ledger scores the stated confidence against the outcome you supplied. "
                   "It does not verify that a claimed success is true. Prefer an outcome source the agent does not control.")


def _public_dir() -> Path:
    return Path(os.environ.get("MIZAN_LEDGER_DIR", ".mizan")).expanduser()


def _private_dir() -> Path:
    p = os.environ.get("MIZAN_PRIVATE_DIR")
    return Path(p).expanduser() if p else _public_dir() / "private"


def _default_agent_id() -> str:
    return os.environ.get("MIZAN_AGENT_ID", "mcp-agent")


def _writable_ledger() -> Ledger:
    return Ledger(_public_dir(), private_path=_private_dir())


mcp = MCPServer(
    "mizan-ledger",
    version=__version__,
    instructions=("Local, offline calibration ledger. Commit a confidence before a task, resolve it with the real outcome, "
                  "read Brier/ECE/log loss. No network. Scores measure calibration only, not whether an agent is good or safe."),
)

_WRITE = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=False)
_READ = ToolAnnotations(readOnlyHint=True, openWorldHint=False)


@mcp.tool(annotations=_WRITE)
def commit_confidence(task_id: str, p: float, agent_id: Optional[str] = None,
                      agent_version: Optional[str] = None) -> dict[str, Any]:
    """Commit a stated confidence p in [0, 1] for task_id BEFORE the outcome is known.

    Appends a hash-only receipt to the local ledger. p and the salt stay in the private
    openings file until resolve_outcome. A declaration cannot be replaced.
    """
    if not isinstance(task_id, str) or not task_id.strip():
        raise LedgerToolError("task_id is required")
    if isinstance(p, bool) or not isinstance(p, (int, float)):
        raise LedgerToolError("p must be a number in [0, 1]")
    try:
        rec = _writable_ledger().declare(task_id, float(p), agent_id or _default_agent_id(),
                                         agent_version or "unspecified")
    except LedgerError as e:
        raise LedgerToolError(str(e)) from e
    out = dict(rec)
    out["task_id"] = task_id
    out["note"] = "Opening (p, salt) kept private until resolve_outcome. This is not a claim that the task succeeded."
    return out


@mcp.tool(annotations=_WRITE)
def resolve_outcome(task_id: str, outcome: bool, agent_id: Optional[str] = None,
                    agent_version: Optional[str] = None) -> dict[str, Any]:
    """Record the real boolean outcome for a committed task and reveal its opening.

    Outcomes cannot be changed once recorded. The caller supplies the outcome; this server
    cannot tell whether it is honest.
    """
    if not isinstance(outcome, bool):
        raise LedgerToolError("outcome must be true or false")
    try:
        rec = _writable_ledger().resolve(task_id, outcome, agent_id=agent_id, version=agent_version)
    except LedgerError as e:
        raise LedgerToolError(str(e)) from e
    out = dict(rec)
    out["warning"] = OUTCOME_WARNING
    return out


@mcp.tool(annotations=_READ)
def get_calibration(agent_id: Optional[str] = None, agent_version: Optional[str] = None) -> dict[str, Any]:
    """Read calibration scores from the local public ledger (read-only).

    Returns n, Brier with 95% interval, ECE, log loss, accuracy, and declared / resolved /
    pending coverage. Small n means wide intervals; ECE is biased upward for small samples.
    """
    d = _public_dir()
    aid = agent_id or _default_agent_id()
    if not d.is_dir():
        out: dict[str, Any] = {"n": 0, "declared": 0, "resolved": 0, "pending": 0, "coverage": None}
    else:
        # Ledger() would mkdir; the directory exists, and summary reads only the public files.
        out = Ledger(d, private_path=_private_dir()).summary(aid, agent_version)
    out["agent_id"] = aid
    out["warning"] = "Scores come from this machine's ledger only. They are not a Mizan benchmark rating and not an endorsement."
    return out


@mcp.tool(annotations=_READ)
def verify_ledger(ledger_dir: Optional[str] = None) -> dict[str, Any]:
    """Verify a ledger directory offline (read-only): hash chains, openings and scores.

    Reads only receipts.jsonl and resolutions.jsonl in ledger_dir (default MIZAN_LEDGER_DIR).
    Needs no private files and no network.
    """
    d = Path(ledger_dir).expanduser() if ledger_dir else _public_dir()
    if not d.is_dir():
        raise LedgerToolError(f"not a directory: {d}")
    rep = verify_ledger_dir(d)
    rep["ledger_dir"] = str(d)
    rep["note"] = "A valid chain proves the files were not edited after the fact relative to each other; timestamps are the local clock until the chain head is anchored independently."
    return rep


@mcp.tool(annotations=_WRITE)
def should_act(p: float, risk_threshold: float, agent_id: Optional[str] = None, agent_version: Optional[str] = None,
               task_id: Optional[str] = None, commit: bool = False) -> dict[str, Any]:
    """Pre-action gate: ACT only if the agent's confidence p >= risk_threshold, otherwise ABSTAIN with a reason.

    If the local ledger holds >= 50 resolved records for the agent, p is adjusted with the agent's own reliability
    table and both raw and adjusted p are returned; with fewer, raw p is used and the reason says so. With
    commit=true and a task_id, the stated p is declared in the ledger first, so the task is scored whether the agent
    acts or abstains. Writes nothing unless commit=true.
    """
    for name, v in (("p", p), ("risk_threshold", risk_threshold)):
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            raise LedgerToolError(f"{name} must be a number in [0, 1]")
    aid = agent_id or _default_agent_id()
    d = _public_dir()
    led = None
    if commit:
        led = _writable_ledger()
    elif d.is_dir():
        led = Ledger(d, private_path=_private_dir())   # directory exists; pairs() reads only public files
    try:
        dec = _should_act(float(p), float(risk_threshold), ledger=led, agent_id=aid, version=agent_version,
                          task_id=task_id, commit=bool(commit))
    except LedgerError as e:
        raise LedgerToolError(str(e)) from e
    out = dec.to_dict()
    out["agent_id"] = aid
    out["note"] = "Based on stated confidence and past calibration only; it cannot verify the agent's reasoning."
    return out


def main() -> None:
    mcp.run("stdio")


if __name__ == "__main__":
    main()
