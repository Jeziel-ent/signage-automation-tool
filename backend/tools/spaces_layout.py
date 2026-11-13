"""Create the empty signage-automation/ folders in the Spaces bucket (needs SIGNAGE_SPACES_* in the environment).

    python tools/spaces_layout.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import spaces  # noqa: E402

cfg = spaces.backup_settings()
if cfg is None:
    sys.exit("Spaces is not configured: set SIGNAGE_SPACES_KEY, SIGNAGE_SPACES_SECRET and SIGNAGE_SPACES_BUCKET")
made = spaces.ensure_layout(spaces._client(cfg), cfg["bucket"])
print(f"bucket {cfg['bucket']}: created {len(made)} folder(s)" + "".join(f"\n  {k}" for k in made))
