"""Re-read the stored Corel Intelligence corrections from their saved editor edits (after the learner learned something new).

    python tools/recapture_corrections.py [--all]

By default only PENDING records are re-derived; --all also re-derives approved / rejected ones (their status is kept). Dataset seeds are
never touched. Needs each shop's cached scene (it exists once the board has been opened in the editor) and its saved edits.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import main  # noqa: E402

if __name__ == "__main__":
    statuses = ("pending", "approved", "rejected") if "--all" in sys.argv else ("pending",)
    print(main.recapture_corrections(statuses))
