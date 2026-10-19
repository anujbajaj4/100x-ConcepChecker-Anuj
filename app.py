import json
import os
from datetime import datetime, timezone
from pathlib import Path

import requests
from dotenv import load_dotenv
from fastapi import FastAPI, Header, HTTPException
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from supabase import create_client

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
# Revise links per subtopic. Empty ones are still to be filled in.
NOTES = {
    "Designing a Good Spec": "",
    "Golden Dataset and Verifiers": "",
    "Building the UI": "",
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


# --- Storage, in Supabase -----------------------------------------------------------
# Each test is a row in the tests table, so a refresh or a fresh Vercel machine
# never loses your place. Each settled question is a row in attempts.
# Like c8-db's step 08, the server talks to the tables through the SDK with the secret key.
# This client is only used for tables, never to log anyone in, so learners never mix.
_db = None


def db():
    global _db
    if _db is None:
        url, key = os.environ.get("SUPABASE_URL", ""), os.environ.get("SUPABASE_SERVICE_KEY", "")
        if not url or not key:
            raise HTTPException(500, "SUPABASE_URL or SUPABASE_SERVICE_KEY is missing from .env.")
        _db = create_client(url, key)
    return _db


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
    )
    if r.status_code != 200:
        # 502: our server is fine, the machine behind it failed.
        raise HTTPException(502, f"Groq replied {r.status_code}: {r.text[:200]}")
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


def load_test(test_id, user_id):
    rows = db().table("tests").select("state").eq("id", test_id).eq("user_id", user_id).execute().data
    if not rows:
        raise HTTPException(404, "That test wasn't found. Start a new one from the map.")
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
# and our server, which holds the key, asks Supabase. The same four dials:
#   sign up:  POST /auth/v1/signup                    {email, password} in, a token out
#   log in:   POST /auth/v1/token?grant_type=password {email, password} in, a token out
#   who is this?  GET /auth/v1/user  with the learner's token in Authorization
# We call these with plain requests instead of the SDK: the SDK keeps one logged-in
# learner inside the client, and on a server shared by many learners that would mix them up.


def supabase_auth(method, path, token=None, **kwargs):
    url, key = os.environ.get("SUPABASE_URL", ""), os.environ.get("SUPABASE_SERVICE_KEY", "")
    if not url or not key:
        raise HTTPException(500, "SUPABASE_URL or SUPABASE_SERVICE_KEY is missing from .env.")
    headers = {"apikey": key, "Authorization": "Bearer " + (token or key)}
    return requests.request(method, url + "/auth/v1" + path, headers=headers, **kwargs)


def auth_error(r):
    # Supabase explains a refusal in one of these fields, for example "Invalid login credentials".
    data = r.json() if r.text else {}
    message = data.get("msg") or data.get("error_description") or data.get("message") or "Login failed."
    # A 4xx is the learner's to fix (wrong password, email taken); anything else is Supabase failing.
    return HTTPException(r.status_code if 400 <= r.status_code < 500 else 502, message)


# Who sent this request? Check the learner's token with Supabase, every time.
def current_user(authorization):
    token = authorization.removeprefix("Bearer ").strip()
    if not token:
        raise HTTPException(401, "Log in first.")
    r = supabase_auth("GET", "/user", token=token)
    if r.status_code != 200:
        raise HTTPException(401, "Your login has expired. Log in again.")
    return r.json()


app = FastAPI()


# The traffic police for login: an email and a password, both text.
class LoginIn(BaseModel):
    email: str
    password: str


# The traffic police for chat: which test (none yet means "start one"), the subtopic and the message.
class ChatIn(BaseModel):
    test_id: int | None = None
    subtopic: str
    message: str


@app.post("/api/signup")
def signup_route(body: LoginIn):
    r = supabase_auth("POST", "/signup", json={"email": body.email, "password": body.password})
    if not r.ok:
        raise auth_error(r)
    data = r.json()
    if "access_token" not in data:
        # Email confirmation is switched on in Supabase, so there is no login yet.
        return {"token": None, "message": "Check your inbox to confirm your email, then log in."}
    return {"token": data["access_token"], "email": data["user"]["email"]}


@app.post("/api/login")
def login_route(body: LoginIn):
    r = supabase_auth("POST", "/token?grant_type=password", json={"email": body.email, "password": body.password})
    if not r.ok:
        raise auth_error(r)
    data = r.json()
    return {"token": data["access_token"], "email": data["user"]["email"]}


@app.get("/api/subtopics")
def subtopics_route():
    return {"subtopics": SUBTOPICS}


@app.post("/api/chat")
def chat_route(body: ChatIn, authorization: str = Header(default="")):
    user = current_user(authorization)  # 401 here if the learner isn't logged in
    if body.subtopic not in SUBTOPICS:
        raise HTTPException(400, "Unknown subtopic: " + body.subtopic)

    # No test yet: create the row, and its id is how the page refers to this test from now on.
    if body.test_id is None:
        s = new_test(body.subtopic)
        row = db().table("tests").insert({"user_id": user["id"], "subtopic": body.subtopic, "state": stored(s)}).execute().data[0]
        return {"test_id": row["id"], "messages": [intro(s), question(s)], "state": state(s)}

    # Otherwise read where this learner was, answer, then write it back.
    s = load_test(body.test_id, user["id"])
    messages = respond(s, body.message)
    for attempt in s["new_attempts"]:
        db().table("attempts").insert({"test_id": body.test_id, **attempt}).execute()
    update = {"state": stored(s)}
    if s["new_attempts"] and s["index"] >= len(s["pairs"]):
        update["score"] = percent(s)  # stored as a percentage, which is what colours the map
        update["completed_at"] = datetime.now(timezone.utc).isoformat()
    db().table("tests").update(update).eq("id", body.test_id).execute()
    return {"test_id": body.test_id, "messages": messages, "state": state(s)}


# The map: every topic and subtopic, with this learner's latest and best score on each.
@app.get("/api/home")
def home_route(authorization: str = Header(default="")):
    user = current_user(authorization)
    finished = (
        db().table("tests").select("subtopic, score, completed_at")
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
