"""What stopping the stack would interrupt: "UPLOADS JOBS" on one line.

start-docker.sh and start-docker.ps1 run this inside the app container
(`docker compose exec -T app python scripts/stack_activity.py`) before they stop
the stack. It asks the running app (/internal/activity, which answers only from
inside its container). An app from before that route can only be asked for its
jobs, through the database: UPLOADS is then "?", since an upload is not in the
database until it has arrived.
"""

import json
import sys
import urllib.request

try:
    with urllib.request.urlopen("http://127.0.0.1:8000/internal/activity", timeout=10) as r:
        answer = json.load(r)
    print(answer["uploads_in_flight"], answer["jobs_active"])
except Exception:
    sys.path.insert(0, "/app")
    from app.api.db import active_job_count

    print("?", active_job_count())
