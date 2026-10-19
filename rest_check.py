# Step 4: talk to Supabase with plain requests, no SDK, like c8-db's 07-supabase-sdk/rest.py.
# Every table gets a URL: {SUPABASE_URL}/rest/v1/{table}
# Run it with: python3 rest_check.py
# It creates a practice learner, a test and an attempt, reads them back, then deletes all three.
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent / ".env")

BASE = os.environ["SUPABASE_URL"]
KEY = os.environ["SUPABASE_SERVICE_KEY"]
HEADERS = {
    "apikey": KEY,                          # which project is calling
    "Authorization": f"Bearer {KEY}",       # what this caller is allowed to do
    "Prefer": "return=representation",      # send back the rows we changed
}


def show(label, r):
    print(f"{label}: {r.status_code} {r.reason}")
    if not r.ok:
        print("  ", r.text[:300])
        if r.status_code in (401, 403):
            print("   Is SUPABASE_SERVICE_KEY in .env the secret key (sb_secret_...)?")
        raise SystemExit(1)
    return r.json() if r.text else None


# 0. A LEARNER. Learners live in Supabase Auth, one more machine at an address: {SUPABASE_URL}/auth/v1.
#    The admin route needs the secret key. Supabase stores the password hashed, never as text.
email = f"practice-{uuid.uuid4().hex[:8]}@example.com"
learner = show("create learner", requests.post(
    f"{BASE}/auth/v1/admin/users", headers=HEADERS,
    json={"email": email, "password": "practice-only-" + uuid.uuid4().hex[:8], "email_confirm": True},
))
user_id = learner["id"]
print("   user id:", user_id)

# 1. CREATE a test: POST a JSON row. The database fills in id and created_at.
test = show("create test", requests.post(
    f"{BASE}/rest/v1/tests", headers=HEADERS,
    json={"user_id": user_id, "subtopic": "Status Codes", "state": {"index": 0}},
))[0]
print("   test id:", test["id"])

# 2. CREATE an attempt that points at that test through test_id, the pointer on the many side.
attempt = show("create attempt", requests.post(
    f"{BASE}/rest/v1/attempts", headers=HEADERS,
    json={"test_id": test["id"], "question_id": "api-status-01", "points": 1,
          "turns": [{"answer": "200 means the request was fine, the list is empty because nothing exists yet"}]},
))[0]

# 3. READ: GET with filters in the query string. eq. means "=".
#    select=*,attempts(*) follows the test_id pointer and brings each test's attempts along.
rows = show("read tests", requests.get(
    f"{BASE}/rest/v1/tests", headers=HEADERS,
    params={"user_id": f"eq.{user_id}", "select": "*,attempts(*)"},
))
print("   tests for this learner:", len(rows), "| attempts inside the first:", len(rows[0]["attempts"]))

# 4. UPDATE: PATCH the rows that match the filter. Finish the test with a score.
show("finish test", requests.patch(
    f"{BASE}/rest/v1/tests", headers=HEADERS, params={"id": f"eq.{test['id']}"},
    json={"score": 1, "completed_at": datetime.now(timezone.utc).isoformat()},
))

# 5. DELETE, many side first: an attempt points at its test, so the attempt goes before the test.
show("delete attempt", requests.delete(f"{BASE}/rest/v1/attempts", headers=HEADERS, params={"id": f"eq.{attempt['id']}"}))
show("delete test", requests.delete(f"{BASE}/rest/v1/tests", headers=HEADERS, params={"id": f"eq.{test['id']}"}))
show("delete learner", requests.delete(f"{BASE}/auth/v1/admin/users/{user_id}", headers=HEADERS))
print("All four dials worked, and the practice rows are gone.")
