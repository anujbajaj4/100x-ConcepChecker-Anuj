import json
import os
import time
from collections import defaultdict, deque
from datetime import datetime, timezone
from pathlib import Path

import requests
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field
from supabase import ClientOptions, create_client

from prompts import EXPLAIN_PROMPT, FOLLOW_UP_PROMPT, GRADER_PROMPT

# This folder holds prompts.py, golden_pairs.json, public/ and your .env.
ROOT = Path(__file__).resolve().parent

# Read GROQ_API_KEY from .env, so there is nothing to export in the terminal.
# On Vercel there is no .env; the key comes from the project's environment variables.
load_dotenv(ROOT / ".env")

PAIRS = json.load(open(ROOT / "golden_pairs.json"))["pairs"]
SUBTOPICS = list(dict.fromkeys(p["subtopic"] for p in PAIRS))
MAX_FOLLOW_UPS = 2

L4 = "https://100x-curriculum.vercel.app/c8/module-2/lecture-04-the-second-interface/post-lecture/notes"
L5 = "https://100x-curriculum.vercel.app/c8/module-2/lecture-05-building-apis/notes"
L6 = "https://100x-curriculum.vercel.app/c8/module-2/lecture-06-databases-and-domain-modelling/notes"
L7 = "https://100x-curriculum.vercel.app/c8/module-2/lecture-07-connect-backend-to-the-database/notes"
L8 = "https://100x-curriculum.vercel.app/c8/module-2/lecture-08-end-to-end-mvp/notes"
# The 100xEngineers Labs Resources doc, one link per tab.
LABS = "https://docs.google.com/document/d/1hmt3C81bok7wzrSx2nn1xEvJleeBbumnyQugXcklETg/edit?tab="
# Revise links per subtopic.
NOTES = {
    "Designing a Good Spec": LABS + "t.oplctwoftgnj",       # Discovery Document (Aarav's example)
    "Golden Dataset and Verifiers": L5,                     # no tab covers this; Lecture 5, "The Verifier Most People Skipped"
    "Building the UI": LABS + "t.tuwo8ndt75pq",             # MVP Plan (Aarav's example)
    "Anatomy of an API Request": L4,
    "HTTP Methods (CRUD)": L4,
    "Status Codes": L4,
    "The Process Layer": L5,
    "Raw Requests in Python": L5,
    "FastAPI and Validation": L7,
    "Where Storage Lives": L6,
    "Domain Modelling Concepts": L6,
    "Database Integration": L8,
}

ROOT_TITLE = "Building APIs, UIs, and Backends"
# The concepts inside each subtopic, shown on the map. They live in the notes and questions.
CONCEPTS = {
    "Designing a Good Spec": ["Observation", "Hypothesis", "Named User"],
    "Golden Dataset and Verifiers": ["Golden Pairs", "Verifier", "Test-Driven Development"],
    "Building the UI": ["Interface Choice", "Gradio Library", "Manual MVP"],
    "Anatomy of an API Request": ["Address", "Action", "Key", "Package"],
    "HTTP Methods (CRUD)": ["Create (POST)", "Read (GET)", "Update (PUT or PATCH)", "Delete (DELETE)"],
    "Status Codes": ["2xx success", "4xx client errors", "5xx server errors"],
    "The Process Layer": ["Prompt plus input", "Deterministic systems", "Abstraction vs understanding"],
    "Raw Requests in Python": ["requests library", "Environment variables", "Model integration"],
    "FastAPI and Validation": ["FastAPI framework", "Pydantic validation", "Server deployment"],
    "Where Storage Lives": ["Client storage", "Server storage", "File persistence"],
    "Domain Modelling Concepts": ["Entities", "Attributes", "Relationships"],
    "Database Integration": ["PostgreSQL", "Supabase", "Row Level Security"],
}


# --- Keys: the least power that does the job ------------------------------------
# app.py only ever holds Supabase's publishable key. On its own it can't read a single
# row: every table request also carries the learner's own login token, and Supabase's
# row level security (security.sql) lets each learner touch only their own rows.
# The secret key stays in .env for the admin scripts (rest_check.py, rls_check.py)
# and is never given to the app or to Vercel.
def supabase_settings():
    url, key = os.environ.get("SUPABASE_URL", ""), os.environ.get("SUPABASE_PUBLISHABLE_KEY", "")
    if not url or not key:
        raise HTTPException(500, "SUPABASE_URL or SUPABASE_PUBLISHABLE_KEY is missing from .env.")
    return url, key


# --- Storage, in Supabase -----------------------------------------------------------
# Each test is a row in the tests table, so a refresh or a fresh Vercel machine
# never loses your place. Each settled question is a row in attempts.
# Like c8-db's step 08, the server talks to the tables through the SDK, but as the
# learner: a fresh client per request, carrying that learner's token, so learners never mix.
def learner_db(token):
    url, key = supabase_settings()
    client = create_client(url, key, options=ClientOptions(auto_refresh_token=False, persist_session=False))
    client.postgrest.auth(token)
    return client


def ask_groq(system, user):
    key = os.environ.get("GROQ_API_KEY", "")
    if not key or key == "paste_your_key_here":
        raise HTTPException(500, "GROQ_API_KEY is missing. Paste your key into the .env file, then restart python3 app.py.")
    r = requests.post(
        "https://api.groq.com/openai/v1/chat/completions",
        headers={"Authorization": "Bearer " + key},
        json={
            "model": "openai/gpt-oss-120b",
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "response_format": {"type": "json_object"},
        },
        timeout=60,
    )
    if r.status_code == 429:
        # Groq's free tier: 30 requests a minute and 1,000 a day, shared by every learner.
        # Pass its 429 on, with how long to wait, instead of retrying and making it worse.
        wait = r.headers.get("retry-after", "30")
        raise HTTPException(429, f"The AI is handling too many requests right now. Try again in {wait} seconds.", headers={"Retry-After": wait})
    if r.status_code != 200:
        # 502: our server is fine, the machine behind it failed. Its error text stays in our logs, not the page.
        print("Groq error", r.status_code, r.text[:300])
        raise HTTPException(502, f"The AI service replied {r.status_code}. Try again in a moment.")
    return json.loads(r.json()["choices"][0]["message"]["content"])


# Each turn is [what was asked, the learner's answer, the key point a follow-up aimed at].
def conversation(s):
    lines = ["Learner's first answer: " + s["turns"][0][1]]
    for turn in s["turns"][1:]:
        lines.append("Follow-up: " + turn[0] + "\nLearner's reply: " + turn[1])
    return "\n\n".join(lines)


def key_point_text(pair, ids):
    return "\n".join(k["id"] + ": " + k["text"] for k in pair["key_points"] if k["id"] in ids)


# AI decides: which key points the conversation shows, and the recited sentence.
def grade(pair, s):
    v = pair["verifier"]
    all_ids = [k["id"] for k in pair["key_points"]]
    user = (
        "Question:\n" + pair["question"]
        + "\n\nKey points:\n" + key_point_text(pair, all_ids)
        + "\n\nRecitation phrases:\n" + ", ".join(v["recitation_flags"])
        + "\n\nMisconceptions:\n" + "; ".join(v["fail_if"])
        + "\n\nConversation so far:\n" + conversation(s)
    )
    return ask_groq(GRADER_PROMPT, user)


# AI writes the nudge, but code picks which missing piece it aims at.
def write_follow_up(pair, s, target):
    got_right = key_point_text(pair, s["met"]) or "Nothing yet."
    user = (
        "Original question:\n" + pair["question"]
        + "\n\nConversation so far:\n" + conversation(s)
        + "\n\nWhat they already got right:\n" + got_right
        + "\n\nThe missing piece:\n" + target["text"]
        + "\n\nThis is follow-up number " + str(s["follow_ups"] + 1) + " of " + str(MAX_FOLLOW_UPS) + "."
    )
    return ask_groq(FOLLOW_UP_PROMPT, user).get("follow_up", "What would happen next in this exact scenario?")


# AI explains the full answer, using only the answer key.
def explain(pair, s):
    answer_key = key_point_text(pair, [k["id"] for k in pair["key_points"]])
    user = (
        "Original question:\n" + pair["question"]
        + "\n\nConversation so far:\n" + conversation(s)
        + "\n\nFull answer key:\n" + answer_key
        + "\n\nWhat they already showed:\n" + (key_point_text(pair, s["met"]) or "Nothing yet.")
    )
    return ask_groq(EXPLAIN_PROMPT, user).get("explanation", "")


# Code decides: which pass-rule key points are still missing.
def missing(pair, met):
    v = pair["verifier"]
    gaps = [k for k in v["required"] if k not in met]
    if v["any_of"] and not met & set(v["any_of"]):
        gaps.append(v["any_of"][0])
    return [k for k in pair["key_points"] if k["id"] in gaps]


def question(s):
    pair = s["pairs"][s["index"]]
    return {"kind": "question", "label": f"Question {s['index'] + 1}: {pair['concept']}", "text": pair["question"]}


def done(s):
    total = len(s["pairs"])
    percent = round(100 * s["score"] / total)
    if percent >= 80:
        status, label = "mastered", "Mastered"
    elif percent >= 50:
        status, label = "getting", "Getting there"
    else:
        status, label = "needs", "Needs work"
    return {
        "kind": "done",
        "status": status,
        "heading": f"Set complete: {s['score']:g} out of {total} ({percent}%). {label}.",
        "text": "Your score is saved, and the map shows it. Try the set again to beat it, or head back to the map.",
    }


def percent(s):
    return round(100 * s["score"] / len(s["pairs"]))


# Record the result, then either wait for "next" or close the set.
def settle(s, points, card):
    pair = s["pairs"][s["index"]]
    # The attempt row for this question, saved by chat_route. Each follow-up keeps the
    # key point it aimed at: the retry points at the gap it was meant to close.
    s["new_attempts"].append({
        "question_id": pair["id"],
        "points": points,
        "recited_sentence": s["recited"],
        "turns": [{"asked": t[0], "answer": t[1], "aimed_at": t[2]} for t in s["turns"]],
    })
    s["results"].append(points)
    s["score"] += points
    s["index"] += 1
    s["turns"] = []
    s["met"] = set()
    s["follow_ups"] = 0
    s["recited"] = None
    if s["index"] < len(s["pairs"]):
        s["waiting"] = True
        return [card, {"kind": "next", "text": f"Say **next** when you're ready for question {s['index'] + 1}."}]
    return [card, done(s)]


def state(s):
    return {
        "topic": s["pairs"][0]["topic"],
        "subtopic": s["subtopic"],
        "notes_url": NOTES.get(s["subtopic"], ""),
        "concepts": [p["concept"] for p in s["pairs"]],
        "results": s["results"],
        "index": s["index"],
        "score": s["score"],
        "waiting": s["waiting"],
        "done": s["index"] >= len(s["pairs"]),
    }


def new_test(subtopic):
    return {
        "subtopic": subtopic,
        "pairs": [p for p in PAIRS if p["subtopic"] == subtopic],
        "index": 0,
        "score": 0,
        "results": [],
        "turns": [],
        "met": set(),
        "follow_ups": 0,
        "waiting": False,
        "recited": None,
        "new_attempts": [],
    }


def intro(s):
    return {
        "kind": "intro",
        "text": f"{len(s['pairs'])} questions on **{s['subtopic']}**. Answer in your own words, the way you'd explain it to a classmate. There's no need for textbook phrasing.",
    }


# What goes in the tests.state column: everything except what code can rebuild.
def stored(s):
    keep = {k: v for k, v in s.items() if k not in ("pairs", "met", "new_attempts")}
    keep["met"] = sorted(s["met"])
    return keep


def load_test(db, test_id):
    # Row level security only shows this learner their own tests. Another learner's
    # test comes back empty, exactly as if it didn't exist, so we answer 403: you're
    # logged in, but this isn't yours to open.
    rows = db.table("tests").select("state").eq("id", test_id).execute().data
    if not rows:
        raise HTTPException(403, "That test isn't yours to open. Start a new one from the map.")
    s = rows[0]["state"]
    s["pairs"] = [p for p in PAIRS if p["subtopic"] == s["subtopic"]]
    s["met"] = set(s["met"])
    s["new_attempts"] = []
    return s


def respond(s, message):
    if s["index"] >= len(s["pairs"]):
        return [{"kind": "next", "text": "This test is complete. Try the set again, or head back to the map."}]

    # A question was just settled: wait until the learner asks for the next one.
    if s["waiting"]:
        if "next" in message.lower():
            s["waiting"] = False
            return [question(s)]
        return [{"kind": "next", "text": f"Say **next** when you're ready for question {s['index'] + 1}."}]

    pair = s["pairs"][s["index"]]
    if s["turns"]:
        s["turns"][-1][1] = message
    else:
        s["turns"].append([pair["question"], message, None])

    result = grade(pair, s)
    # Credit carries across replies: a point shown once stays shown.
    s["met"] |= set(result.get("key_points_met", []))
    s["recited"] = s["recited"] or result.get("recited_sentence")
    gaps = missing(pair, s["met"])

    if not gaps and not result.get("fail_if_hit"):
        if s["follow_ups"] == 0:
            card = {"kind": "passed", "heading": "Nicely reasoned. 1 point.", "text": result.get("note", "")}
            return settle(s, 1, card)
        card = {"kind": "half", "heading": "You got there. Half a point for the nudge.", "text": result.get("note", "")}
        return settle(s, 0.5, card)

    if s["follow_ups"] < MAX_FOLLOW_UPS:
        target = gaps[0] if gaps else pair["key_points"][0]
        follow_up = write_follow_up(pair, s, target)
        s["follow_ups"] += 1
        s["turns"].append([follow_up, "", target["id"]])
        quote = result.get("recited_sentence") if s["follow_ups"] == 1 else None
        if quote:
            heading = "This part sounds a little memorised."
            text = "The rest is your own thinking. Let's dig into the underlined bit:"
        elif s["met"]:
            heading = "You're on the right track."
            text = result.get("note", "") + " One more piece to work out:"
        else:
            heading = "No worries, let's take it one step at a time."
            text = "Think about this:"
        return [{"kind": "follow_up", "heading": heading, "text": text, "question": follow_up, "quote": quote}]

    # Out of nudges: walk through the full answer, then show the whole answer key.
    card = {
        "kind": "missed",
        "heading": "Let's walk through it together.",
        "text": explain(pair, s),
        "key_points": [{"text": k["text"], "had": k["id"] in s["met"]} for k in pair["key_points"]],
    }
    return settle(s, 0, card)


# --- Login, through Supabase Auth ------------------------------------------------
# Supabase Auth is one more machine at an address: {SUPABASE_URL}/auth/v1.
# The page never talks to it. The page sends the email and password to our server,
# and our server asks Supabase. The same four dials:
#   sign up:  POST /auth/v1/signup                    {email, password} in, a token out
#   log in:   POST /auth/v1/token?grant_type=password {email, password} in, a token out
#   who is this?  GET /auth/v1/user  with the learner's token in Authorization
#   log out:  POST /auth/v1/logout   ends the session at Supabase too
# We call these with plain requests instead of the SDK: the SDK keeps one logged-in
# learner inside the client, and on a server shared by many learners that would mix them up.
#
# The token lives in an HttpOnly cookie that our server sets. The page's JavaScript
# can't read it, so a script injected into the page can't steal it, and the browser
# only sends it back to our own /api address.
TOKEN_COOKIE = "cc_token"


def supabase_auth(method, path, token=None, **kwargs):
    url, key = supabase_settings()
    headers = {"apikey": key, "Authorization": "Bearer " + (token or key)}
    return requests.request(method, url + "/auth/v1" + path, headers=headers, timeout=15, **kwargs)


def auth_error(r):
    # Supabase explains a refusal in one of these fields, for example "Invalid login credentials".
    data = r.json() if r.text else {}
    message = data.get("msg") or data.get("error_description") or data.get("message") or "Login failed."
    # A 4xx is the learner's to fix (wrong password, email taken); anything else is Supabase failing.
    return HTTPException(r.status_code if 400 <= r.status_code < 500 else 502, message)


# Who sent this request? Check the learner's token with Supabase, every time.
# 401 means "we don't know who you are": no token, or one that has expired.
def current_user(request):
    token = request.cookies.get(TOKEN_COOKIE) or request.headers.get("authorization", "").removeprefix("Bearer ").strip()
    if not token:
        raise HTTPException(401, "Log in first.")
    r = supabase_auth("GET", "/user", token=token)
    if r.status_code != 200:
        raise HTTPException(401, "Your login has expired. Log in again.")
    user = r.json()
    user["token"] = token
    return user


# --- Rate limits -------------------------------------------------------------------
# A sliding window: remember when each caller last hit us, and turn them back with
# 429 and a Retry-After once they go over. These counters live in this server's
# memory, so on Vercel each machine keeps its own. There's no daily cap per learner:
# if Groq's shared 1,000 requests a day run out, its own 429 reaches the learner.
LOGINS_PER_MINUTE = 10    # per IP address, against password guessing
ANSWERS_PER_MINUTE = 15   # per learner, each answer can mean up to three Groq calls
PAGES_PER_MINUTE = 60     # per learner, for the map
WINDOWS = defaultdict(deque)


def limit(key, most, seconds):
    now = time.monotonic()
    hits = WINDOWS[key]
    while hits and hits[0] <= now - seconds:
        hits.popleft()
    if len(hits) >= most:
        wait = int(hits[0] + seconds - now) + 1
        raise HTTPException(429, f"Too many requests. Try again in {wait} seconds.", headers={"Retry-After": str(wait)})
    hits.append(now)


def client_ip(request):
    # Behind Vercel, the caller's real address is the first one in this header.
    forwarded = request.headers.get("x-forwarded-for", "")
    return forwarded.split(",")[0].strip() or (request.client.host if request.client else "unknown")


def is_https(request):
    # Vercel ends HTTPS at its edge and tells us so in this header.
    return request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https"


def start_session(request, response, data):
    response.set_cookie(
        TOKEN_COOKIE, data["access_token"],
        max_age=data.get("expires_in", 3600),  # the token's own lifetime: an hour
        httponly=True,                         # page scripts can't read it
        secure=is_https(request),              # only over HTTPS once deployed
        samesite="strict",                     # never sent from another site's page
        path="/api",                           # only sent to our API
    )
    return {"email": data["user"]["email"]}


# The interactive API docs at /docs are handy on your laptop, but in production they'd
# hand every visitor a map of the API. Vercel sets VERCEL=1, so they switch off there.
DOCS = not os.environ.get("VERCEL")
app = FastAPI(docs_url="/docs" if DOCS else None, redoc_url=None, openapi_url="/openapi.json" if DOCS else None)

# --- Security headers ----------------------------------------------------------------
# Instructions to the browser, sent with every response. vercel.json sends the same set
# for the page on Vercel, where its CDN serves public/ without going through this code.
SECURITY_HEADERS = {
    # Only run scripts, styles and fonts from our own address (and Google Fonts), only
    # fetch from our own API, and never let another site put this page in a frame.
    "Content-Security-Policy": (
        "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
        "font-src https://fonts.gstatic.com; img-src 'self' data:; connect-src 'self'; "
        "object-src 'none'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'"
    ),
    "Strict-Transport-Security": "max-age=63072000; includeSubDomains",  # HTTPS only, once deployed
    "X-Content-Type-Options": "nosniff",                                 # a file is what it says it is
    "X-Frame-Options": "DENY",                                           # older browsers' frame-ancestors
    "Referrer-Policy": "strict-origin-when-cross-origin",               # don't leak page paths to other sites
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",   # this app needs none of them
}


@app.middleware("http")
async def security_headers(request, call_next):
    response = await call_next(request)
    for name, value in SECURITY_HEADERS.items():
        response.headers.setdefault(name, value)
    if request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"  # scores and logins are never cached
    return response


# No CORS middleware on purpose: the browser only lets pages on our own address call
# this API, so another website can't call it from a visitor's browser.


# --- The traffic police ------------------------------------------------------------
# Every package is checked before any route code runs: the right fields, the right
# types, sensible lengths, and nothing extra. Anything else is turned back with a 400
# that names the rule it broke.
class LoginIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    email: str = Field(max_length=254, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    password: str = Field(min_length=6, max_length=72)


class ChatIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    test_id: int | None = Field(default=None, gt=0)       # none yet means "start one"
    subtopic: str = Field(max_length=100)
    message: str = Field(min_length=1, max_length=2000)  # long enough to explain, short enough to stop abuse


@app.exception_handler(RequestValidationError)
def bad_package(request, exc):
    first = exc.errors()[0]
    field = ".".join(str(part) for part in first["loc"] if part != "body")
    if first["type"] == "json_invalid":
        reason = "The package isn't valid JSON."
    elif first["type"] == "string_pattern_mismatch" and field == "email":
        reason = "That doesn't look like an email address."
    else:
        reason = (field + ": " if field else "") + first["msg"]
    return JSONResponse(status_code=400, content={"detail": reason})


@app.post("/api/signup")
def signup_route(body: LoginIn, request: Request, response: Response):
    limit("login:" + client_ip(request), LOGINS_PER_MINUTE, 60)
    r = supabase_auth("POST", "/signup", json={"email": body.email, "password": body.password})
    if not r.ok:
        raise auth_error(r)
    data = r.json()
    if "access_token" not in data:
        # Email confirmation is switched on in Supabase, so there is no login yet.
        return {"email": None, "message": "Check your inbox to confirm your email, then log in."}
    return start_session(request, response, data)


@app.post("/api/login")
def login_route(body: LoginIn, request: Request, response: Response):
    limit("login:" + client_ip(request), LOGINS_PER_MINUTE, 60)
    r = supabase_auth("POST", "/token?grant_type=password", json={"email": body.email, "password": body.password})
    if not r.ok:
        raise auth_error(r)
    return start_session(request, response, r.json())


@app.post("/api/logout")
def logout_route(request: Request, response: Response):
    token = request.cookies.get(TOKEN_COOKIE)
    if token:
        supabase_auth("POST", "/logout", token=token)  # end the session at Supabase as well
    response.delete_cookie(TOKEN_COOKIE, path="/api")
    return {"ok": True}


@app.post("/api/chat")
def chat_route(body: ChatIn, request: Request):
    user = current_user(request)  # 401 here if the learner isn't logged in
    limit("answers:" + user["id"], ANSWERS_PER_MINUTE, 60)  # 429 here if they're sending too fast
    if body.subtopic not in SUBTOPICS:
        raise HTTPException(400, "Unknown subtopic: " + body.subtopic)
    db = learner_db(user["token"])

    # No test yet: create the row, and its id is how the page refers to this test from now on.
    if body.test_id is None:
        s = new_test(body.subtopic)
        row = db.table("tests").insert({"user_id": user["id"], "subtopic": body.subtopic, "state": stored(s)}).execute().data[0]
        return {"test_id": row["id"], "messages": [intro(s), question(s)], "state": state(s)}

    # Otherwise read where this learner was, answer, then write it back.
    s = load_test(db, body.test_id)  # 403 here if the test isn't theirs
    messages = respond(s, body.message)
    for attempt in s["new_attempts"]:
        db.table("attempts").insert({"test_id": body.test_id, **attempt}).execute()
    update = {"state": stored(s)}
    if s["new_attempts"] and s["index"] >= len(s["pairs"]):
        update["score"] = percent(s)  # stored as a percentage, which is what colours the map
        update["completed_at"] = datetime.now(timezone.utc).isoformat()
    db.table("tests").update(update).eq("id", body.test_id).execute()
    return {"test_id": body.test_id, "messages": messages, "state": state(s)}


# The map: every topic and subtopic, with this learner's latest and best score on each.
@app.get("/api/home")
def home_route(request: Request):
    user = current_user(request)
    limit("pages:" + user["id"], PAGES_PER_MINUTE, 60)
    finished = (
        learner_db(user["token"]).table("tests").select("subtopic, score, completed_at")
        .eq("user_id", user["id"]).not_.is_("completed_at", "null")
        .order("completed_at").execute().data
    )
    scores = {}
    for row in finished:  # oldest first, so the last one seen is the latest
        mine = scores.setdefault(row["subtopic"], {"latest": None, "best": None, "tests": 0})
        mine["latest"] = round(row["score"])
        mine["best"] = max(mine["best"] or 0, round(row["score"]))
        mine["tests"] += 1

    topics = []
    for subtopic in SUBTOPICS:
        topic = next(p["topic"] for p in PAIRS if p["subtopic"] == subtopic)
        if not topics or topics[-1]["title"] != topic:
            topics.append({"title": topic, "subtopics": []})
        topics[-1]["subtopics"].append({
            "title": subtopic,
            "concepts": CONCEPTS.get(subtopic, []),
            "questions": sum(1 for p in PAIRS if p["subtopic"] == subtopic),
            "notes_url": NOTES.get(subtopic, ""),
            **scores.get(subtopic, {"latest": None, "best": None, "tests": 0}),
        })
    return {"title": ROOT_TITLE, "email": user.get("email"), "topics": topics}


# Locally, serve the page from this same server, so the page and the API share
# one address, exactly like on Vercel. On Vercel, its CDN serves public/ itself.
# This mount comes last so the /api routes above are matched first.
PUBLIC = ROOT / "public"
if PUBLIC.is_dir():
    app.mount("/", StaticFiles(directory=PUBLIC, html=True), name="public")


# python3 app.py starts everything: the API and the page, at http://127.0.0.1:8000
# uvicorn is the program that turns the FastAPI app above into a running web server.
# reload=True restarts it whenever you save a file.
if __name__ == "__main__":
    import uvicorn

    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("app:app", app_dir=str(ROOT), port=port, reload=True, reload_dirs=[str(ROOT)])
