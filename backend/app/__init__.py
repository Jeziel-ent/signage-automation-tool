"""Settings from backend/.env (see .env.example) are loaded here, so the API server and the CorelDRAW worker subprocess
(python -m app.corel_worker) both see them. Variables already set in the environment win over the file."""
from pathlib import Path

try:
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parents[1] / ".env", override=False)
except ImportError:  # python-dotenv missing: the environment alone is used
    pass
