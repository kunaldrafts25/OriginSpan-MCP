"""Generate ignored local integration credentials once; never print secrets."""
from pathlib import Path
import secrets

root = Path(__file__).resolve().parents[1]
target = root / ".diamond/integration.env"
target.parent.mkdir(parents=True, exist_ok=True)
if target.exists():
    print("Existing integration credentials retained")
else:
    target.write_text("\n".join(f"{name}={secrets.token_urlsafe(32)}" for name in [
        "DIAMOND_TEST_DB_PASSWORD", "DIAMOND_TEST_ADMIN_PASSWORD", "DIAMOND_TEST_CLIENT_SECRET"]) + "\n")
    print("Created .diamond/integration.env; keep this file with the database volume")
