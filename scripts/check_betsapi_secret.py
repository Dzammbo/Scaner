import os
import sys
from datetime import datetime, timezone

key = os.environ.get("BETSAPI_KEY", "")
print(f"timestamp_utc={datetime.now(timezone.utc).isoformat()}")
print("BETSAPI_KEY_PRESENT=" + ("true" if bool(key) else "false"))
print("BETSAPI_KEY_LENGTH=" + (str(len(key)) if key else "0"))

if not key:
    print("ERROR: BETSAPI_KEY is not available in the runner environment.")
    sys.exit(1)

print("OK: BETSAPI_KEY is available. Value is not printed.")
