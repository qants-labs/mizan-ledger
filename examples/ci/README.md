# Mizan calibration gate in CI

1. Mark tests with the confidence your agent (or you) declare **before** they run:

   ```python
   import pytest

   @pytest.mark.confidence(0.9)
   def test_parser():
       ...
   ```

2. Run the suite once locally and write a baseline (needs at least `--min-n` resolved declarations, default 50):

   ```bash
   pytest --mizan-ledger-dir .mizan --mizan-agent-id my-agent
   mizan-ledger gate .mizan --baseline mizan-baseline.json --write-baseline --agent my-agent
   git add mizan-baseline.json
   ```

3. Copy `mizan-gate.yml` to `.github/workflows/` in your repository.

Exit codes of `mizan-ledger gate`: `0` pass, `1` worse beyond the delta and beyond the 95% interval (or the ledger does not
verify), `2` inconclusive (too few resolved declarations or no baseline; a warning, never a fake pass or fail).
Keep `.mizan/private/` out of version control.
