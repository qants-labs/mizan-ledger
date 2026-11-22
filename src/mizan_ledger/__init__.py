"""mizan-ledger: confidence commitments and honesty scoring. Hash-only, local-first, offline-verifiable."""
from .core import (
    SCHEMA, Ledger, LedgerError, canonical_json, commitment_hash, fmt_p,
    verify_opening, verify_chain, verify_ledger_dir,
)
from .metrics import brier, log_loss, ece, accuracy_wilson, brier_interval, summarize

__version__ = "0.2.0"
__all__ = [
    "SCHEMA", "Ledger", "LedgerError", "canonical_json", "commitment_hash", "fmt_p",
    "verify_opening", "verify_chain", "verify_ledger_dir",
    "brier", "log_loss", "ece", "accuracy_wilson", "brier_interval", "summarize",
]
