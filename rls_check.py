# Prove row level security works, with plain requests, like rest_check.py.
# Run it with: python3 rls_check.py   (after running security.sql in Supabase)
#
# It makes two practice learners, A and B. A starts a test. Then B, logged in as
# themselves, tries to read it, change it and add to it, and someone with no login at
# all tries to read everything. Every one of those should get nothing.
# At the end it deletes both learners and their rows.
import os
import uuid
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent / ".env")

BASE = os.environ["SUPABASE_URL"]
PUBLISHABLE = os.environ["SUPABASE_PUBLISHABLE_KEY"]  # what the app uses
SECRET = os.environ["SUPABASE_SERVICE_KEY"]           # admin only: making and removing the practice learners
ADMIN = {"apikey": SECRET, "Authorization": f"Bearer {SECRET}"}
results = []


def check(label, passed, detail=""):
    results.append(passed)
    print(("PASS  " if passed else "FAIL  ") + label + (f"  ({detail})" if detail else ""))


def as_learner(token):
    return {"apikey": PUBLISHABLE, "Authorization": f"Bearer {token}", "Prefer": "return=representation"}


def make_learner():
    email, password = f"practice-{uuid.uuid4().hex[:8]}@example.com", "practice-" + uuid.uuid4().hex[:10]
    user = requests.post(f"{BASE}/auth/v1/admin/users", headers=ADMIN, json={"email": email, "password": password, "email_confirm": True})
    user.raise_for_status()
    login = requests.post(f"{BASE}/auth/v1/token?grant_type=password", headers={"apikey": PUBLISHABLE}, json={"email": email, "password": password})
    login.raise_for_status()
    return user.json()["id"], login.json()["access_token"]


a_id, a_token = make_learner()
b_id, b_token = make_learner()
tests_url, attempts_url = f"{BASE}/rest/v1/tests", f"{BASE}/rest/v1/attempts"
try:
    # A can start and read their own test.
    r = requests.post(tests_url, headers=as_learner(a_token), json={"user_id": a_id, "subtopic": "Status Codes", "state": {}})
    check("A starts a test of their own", r.status_code == 201, r.status_code)
    test_id = r.json()[0]["id"]
    r = requests.get(tests_url, headers=as_learner(a_token), params={"id": f"eq.{test_id}"})
    check("A reads their own test", r.ok and len(r.json()) == 1)

    # B, logged in as themselves, can't see, change or add to it.
    r = requests.get(tests_url, headers=as_learner(b_token), params={"id": f"eq.{test_id}"})
    check("B can't read A's test", r.ok and r.json() == [], f"{len(r.json())} rows")
    r = requests.patch(tests_url, headers=as_learner(b_token), params={"id": f"eq.{test_id}"}, json={"score": 100})
    check("B can't change A's score", r.ok and r.json() == [], f"{len(r.json())} rows changed")
    r = requests.post(attempts_url, headers=as_learner(b_token), json={"test_id": test_id, "question_id": "x", "points": 1, "turns": []})
    check("B can't add an attempt to A's test", r.status_code in (401, 403), r.status_code)
    r = requests.post(tests_url, headers=as_learner(b_token), json={"user_id": a_id, "subtopic": "Status Codes", "state": {}})
    check("B can't start a test in A's name", r.status_code in (401, 403), r.status_code)

    # No login at all: the publishable key alone sees nothing.
    r = requests.get(tests_url, headers={"apikey": PUBLISHABLE, "Authorization": f"Bearer {PUBLISHABLE}"})
    check("No login reads no tests", r.ok and r.json() == [], f"{len(r.json())} rows" if r.ok else r.status_code)

    # Nobody deletes, not even the owner.
    requests.delete(tests_url, headers=as_learner(a_token), params={"id": f"eq.{test_id}"})
    r = requests.get(tests_url, headers=as_learner(a_token), params={"id": f"eq.{test_id}"})
    check("A can't delete their own test", r.ok and len(r.json()) == 1)
finally:
    # Clean up with the admin key: rows first (many side first), then the learners.
    for uid in (a_id, b_id):
        ids = [t["id"] for t in requests.get(tests_url, headers=ADMIN, params={"user_id": f"eq.{uid}", "select": "id"}).json()]
        for tid in ids:
            requests.delete(attempts_url, headers=ADMIN, params={"test_id": f"eq.{tid}"})
        requests.delete(tests_url, headers=ADMIN, params={"user_id": f"eq.{uid}"})
        requests.delete(f"{BASE}/auth/v1/admin/users/{uid}", headers=ADMIN)

print(f"\n{sum(results)} of {len(results)} checks passed. Practice learners removed.")
