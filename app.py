import json
import os
import re
import time
from collections import defaultdict, deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

import requests
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.concurrency import run_in_threadpool
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field
from supabase import ClientOptions, create_client

from prompts import FOLLOW_UP_PROMPT, LABEL_PROMPT, SAME_ANSWER_PROMPT, WALKTHROUGH_PROMPT

# This folder holds prompts.py, golden_pairs.json, public/ and your .env.
ROOT = Path(__file__).resolve().parent

# Read GROQ_API_KEY from .env, so there is nothing to export in the terminal.
# On Vercel there is no .env; the key comes from the project's environment variables.
load_dotenv(ROOT / ".env")

IDEAS = json.load(open(ROOT / "golden_pairs.json"))["ideas"]
SUBTOPICS = list(dict.fromkeys(i["subtopic"] for i in IDEAS))
# Two segments. Practice is set in Aarav's world and never scored. The real-world test
# asks the same ideas inside apps people use every day, and only it colours the map.
MODES = {"practice": "Practice", "real": "Real-world test"}

# Each real-world case carries a "screen": the app it happens in, drawn by the page from
# a few kinds of block. It only sets the scene; the question and its answer stay in the
# case. Check every screen on startup, so a typo fails loudly here, not in a learner's chat.
SCREEN_BLOCKS = {
    "head": ({"text"}, {"sub"}),
    "person": ({"initials", "name", "sub"}, set()),
    "amount": ({"text"}, set()),
    "pin": ({"label"}, set()),
    "line": ({"label", "value"}, set()),
    "alert": ({"tone", "title"}, {"text"}),
    "msg": ({"from", "text"}, {"name"}),
    "code": ({"text"}, set()),
    "field": ({"label", "value"}, {"bad"}),
    "button": ({"text"}, set()),
    "note": ({"text"}, {"tone"}),
    "text": ({"text"}, set()),
    "table": ({"name", "cols", "rows"}, set()),
    "list": ({"items"}, set()),
    "empty": ({"text"}, set()),
}


def check_screen(where, screen):
    assert set(screen) <= {"app", "bar", "blocks"} and screen.get("app") and screen.get("blocks"), where
    for block in screen["blocks"]:
        need, may = SCREEN_BLOCKS[block["t"]]
        assert need <= set(block) <= need | may | {"t"}, f"{where}: {block}"


for _idea in IDEAS:
    for _n, _case in enumerate(_idea["real_world"]):
        check_screen(f"{_idea['id']} real-world case {_n + 1}", _case["screen"])

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


# --- Checking a short answer: code first ---------------------------------------------
# Every case has one short answer, written before the session, plus words that count as
# right (accept) and words that give a wrong answer away (reject). Code checks those.
# Only when an answer hits neither list, or both, does the model answer one narrow
# question: "does this mean the same as the written answer?"
ENDINGS = ("", "s", "es", "ed", "d", "ing")  # pay matches pays, paid doesn't: keep both in the lists
UNSURE = ("no idea", "not sure", "dont know", "don t know", "do not know", "idk", "no clue")


def words(text):
    text = " " + re.sub(r"[^a-z0-9₹]+", " ", text.lower()).strip() + " "
    for phrase in UNSURE:  # "no idea" must not count as the answer "no"
        text = text.replace(" " + phrase + " ", " ")
    return text.split()


def mentions(answer, phrase):
    said, want = words(answer), words(phrase)
    for start in range(len(said) - len(want) + 1):
        if all(any(said[start + k] == w + end for end in ENDINGS) for k, w in enumerate(want)):
            return True
    return False


def check(case, answer):
    hit = any(mentions(answer, p) for p in case["accept"])
    miss = any(mentions(answer, p) for p in case["reject"])
    if hit and not miss:
        return True, "code"
    if miss and not hit:
        return False, "code"
    user = f"Question: {case['scenario']}\nCorrect answer: {case['answer']}\nLearner's answer: {answer}"
    return bool(ask_groq(SAME_ANSWER_PROMPT, user).get("same")), "model"


# --- The model's narrow jobs -----------------------------------------------------------
def idea_brief(idea):
    return (
        "Idea: " + idea["idea"]
        + "\nWhat real understanding looks like: " + idea["understanding"]
        + "\nThe most common wrong idea: " + idea["misconception"]
    )


# AI decides: is the explanation their own reasoning, recited, wrong, or unclear?
def label(idea, explanation):
    user = (
        idea_brief(idea)
        + "\nStock phrases people recite: " + ", ".join(idea["recitation_markers"])
        + "\n\nLearner's explanation:\n" + explanation
    )
    r = ask_groq(LABEL_PROMPT, user)
    kind = r.get("label") if r.get("label") in ("own", "recited", "wrong", "unclear") else "unclear"
    quote = r.get("quote") if kind in ("recited", "wrong") else None
    return kind, quote


# AI writes the one follow-up, aimed at the sentence it quoted.
def write_follow_up(idea, s):
    user = (
        idea_brief(idea)
        + "\n\nLearner's explanation:\n" + s["explanation"]
        + "\n\nSentence to aim at (" + str(s["label"]) + "): " + (s["quote"] or "none")
        + "\nA question you could build on: " + idea["follow_up_seed"]
    )
    return ask_groq(FOLLOW_UP_PROMPT, user).get("follow_up") or idea["follow_up_seed"]


# AI walks through the answers, using only the cases and their written answers.
def walkthrough(idea, s, case_a, case_b):
    user = (
        idea_brief(idea)
        + "\n\nLearner's explanation:\n" + s["explanation"]
        + "\nSentence that sounded " + str(s["label"]) + ": " + (s["quote"] or "none")
        + f"\n\nCase 1: {case_a['scenario']}\nCorrect answer: {case_a['answer']}\nLearner said: {s['a']['answer']}"
        + f"\n\nCase 2: {case_b['scenario']}\nCorrect answer: {case_b['answer']}\nLearner said: {s['b']['answer']}"
    )
    return ask_groq(WALKTHROUGH_PROMPT, user).get("walkthrough", "")


# --- One test, step by step ------------------------------------------------------------
# For each idea: explain → case A → (if needed) one follow-up → case B.
#   own reasoning + case A right               → 1
#   recited + case A right → case B right      → 1, wrong → 0.5
#   case A wrong, or a wrong explanation       → case B right → 0.5, wrong → 0
# A right answer under a wrong explanation doesn't count: it was right by luck.
def ideas_for(subtopic):
    return [i for i in IDEAS if i["subtopic"] == subtopic]


def cases(s, idea):
    return idea["practice"] if s["mode"] == "practice" else idea["real_world"]


def fresh_idea():
    return {
        "stage": "explain", "explanation": "", "label": None, "quote": None,
        "simpler_used": False, "asked_again": False, "pasted": False,
        "a": None, "follow_up": None, "reply": None, "b": None,
    }


def new_test(subtopic, mode):
    s = {"subtopic": subtopic, "mode": mode, "index": 0, "score": 0, "results": [], "waiting": False, **fresh_idea()}
    s["items"] = ideas_for(subtopic)
    s["new_attempts"] = []
    return s


# What goes in the tests.state column: everything except what code can rebuild.
def stored(s):
    return {k: v for k, v in s.items() if k not in ("items", "new_attempts")}


def load_test(db, test_id):
    # Row level security only shows this learner their own tests. Another learner's
    # test comes back empty, exactly as if it didn't exist, so we answer 403: you're
    # logged in, but this isn't yours to open.
    rows = db.table("tests").select("state").eq("id", test_id).execute().data
    if not rows or "mode" not in rows[0]["state"]:
        raise HTTPException(403, "That test isn't yours to open. Start a new one from the map.")
    s = rows[0]["state"]
    s["items"] = ideas_for(s["subtopic"])
    s["new_attempts"] = []
    return s


def intro(s):
    n, name = len(s["items"]), s["subtopic"]
    if s["mode"] == "practice":
        text = f"**Practice: {n} ideas on {name}**, set in Aarav's world. Nothing here is scored, so take your time. Explain each idea in your own words first; there's no need for textbook phrasing."
    else:
        text = f"**Real-world test: {n} ideas on {name}**, set in apps you use every day. This one counts towards your score. Explain each idea in your own words first, then try it on a new case."
    return {"kind": "intro", "text": text}


def explain_card(s, simpler=False):
    idea = s["items"][s["index"]]
    return {
        "kind": "explain",
        "label": f"Idea {s['index'] + 1} of {len(s['items'])}: {idea['idea']}",
        "text": idea["simpler"] if simpler else idea["explain"],
        "can_simplify": not simpler and not s["simpler_used"],
    }


def case_card(s, which):
    a, b = cases(s, s["items"][s["index"]])
    case = a if which == "a" else b
    card = {"kind": "case", "label": "A new case" if which == "a" else "One more new case", "text": case["scenario"]}
    if "screen" in case:
        card["screen"] = case["screen"]
    return card


def next_card(s):
    return {"kind": "next", "text": f"Say **next** when you're ready for idea {s['index'] + 1}."}


def points_phrase(s, points):
    if s["mode"] == "practice":
        return ""
    return {1: " 1 point.", 0.5: " Half a point.", 0: ""}[points]


def done(s):
    total = len(s["items"])
    if s["mode"] == "practice":
        return {
            "kind": "done", "mode": "practice", "status": None,
            "heading": "Practice complete.",
            "text": "Practice never counts towards your score. When you're ready, take the real-world test: the same ideas, inside apps you use every day.",
        }
    pct = percent(s)
    status, word = ("mastered", "Mastered") if pct >= 80 else ("getting", "Getting there") if pct >= 50 else ("needs", "Needs work")
    return {
        "kind": "done", "mode": "real", "status": status,
        "heading": f"Real-world test complete: {s['score']:g} out of {total} ({pct}%). {word}.",
        "text": "Your score is saved, and the map shows it. Practise again any time, or retake the test to beat it.",
    }


def percent(s):
    return round(100 * s["score"] / len(s["items"]))


# Record this idea's attempt, then wait for "next" or close the set.
def settle(s, points, card):
    idea = s["items"][s["index"]]
    # The attempt row. case_b points back at the quoted sentence and follow-up:
    # the retry points at the gap it was meant to close.
    s["new_attempts"].append({
        "question_id": idea["id"],
        "points": points,
        "recited_sentence": s["quote"] if s["label"] == "recited" else None,
        "turns": {
            "mode": s["mode"], "explanation": s["explanation"], "label": s["label"], "quote": s["quote"],
            "pasted": s["pasted"], "case_a": s["a"], "follow_up": s["follow_up"], "reply": s["reply"], "case_b": s["b"],
        },
    })
    s["results"].append(points)
    s["score"] += points
    s["index"] += 1
    s.update(fresh_idea())
    if s["index"] < len(s["items"]):
        s["waiting"] = True
        return [card, next_card(s)]
    s["stage"] = "done"
    return [card, done(s)]


def respond(s, message, simpler=False, pasted=False):
    if s["stage"] == "done":
        return [{"kind": "next", "text": "This set is complete. Head back to the map to practise again or take a test."}]

    # An idea was just settled: wait until the learner asks for the next one.
    if s["waiting"]:
        if "next" in message.lower():
            s["waiting"] = False
            return [explain_card(s)]
        return [next_card(s)]

    idea = s["items"][s["index"]]
    case_a, case_b = cases(s, idea)

    # "Make it simpler": the gentler version of the explain prompt, written in advance.
    if simpler:
        if s["stage"] == "explain" and not s["simpler_used"]:
            s["simpler_used"] = True
            return [explain_card(s, simpler=True)]
        return []

    # 1. The explanation. Not scored: it tells us which sentence to aim the follow-up at.
    if s["stage"] == "explain":
        s["explanation"] = (s["explanation"] + "\n" + message).strip()
        s["pasted"] = s["pasted"] or pasted
        kind, quote = label(idea, s["explanation"])
        if kind == "unclear" and not s["asked_again"]:
            s["asked_again"] = s["simpler_used"] = True
            return [{"kind": "explain", "label": "Let's try it a simpler way", "text": idea["simpler"], "can_simplify": False}]
        s["label"], s["quote"] = kind, quote
        s["stage"] = "case_a"
        return [{"kind": "intro", "text": "Thanks. Now try the idea on a new case:"}, case_card(s, "a")]

    # 2. Case A: the "before" measure.
    if s["stage"] == "case_a":
        right, how = check(case_a, message)
        s["a"] = {"answer": message, "right": right, "checked_by": how, "pasted": pasted}
        # Only an explanation in their own words lets a right case A stand alone. One that's
        # still unclear after the simpler re-ask gets the follow-up and case B, like a recited one:
        # otherwise "I dont know" plus a lucky guess would earn a full point.
        if right and s["label"] == "own":
            card = {"kind": "passed", "heading": "You understood it and applied it." + points_phrase(s, 1), "text": "That's the idea working on a case you hadn't seen."}
            return settle(s, 1, card)
        s["follow_up"] = write_follow_up(idea, s)
        s["stage"] = "follow_up"
        if s["label"] == "recited":
            heading = "Right answer, but one sentence sounded memorised." if right else "Not quite. Let's look at one sentence you wrote."
            text = "Let's turn the underlined part into a reason:"
        elif s["label"] == "wrong":
            heading = "You got the case. Let's test the reason you gave." if right else "Not quite. Let's test one sentence you wrote."
            text = "Take the underlined part:"
        elif right:
            heading = "Right answer. Now let's put the reason into words."
            text = "Your explanation didn't say why yet. Think about this:"
        else:
            heading = "Not quite. Let's work it out a step at a time."
            text = "Think about this:"
        return [{"kind": "follow_up", "heading": heading, "text": text, "question": s["follow_up"], "quote": s["quote"]}]

    # 3. The follow-up reply. Not scored: it's the retry, kept with the gap it answers.
    if s["stage"] == "follow_up":
        s["reply"] = message
        s["stage"] = "case_b"
        return [{"kind": "intro", "text": "Thanks. Now one more new case:"}, case_card(s, "b")]

    # 4. Case B: the "after" measure, built so the common wrong idea gets it wrong.
    right, how = check(case_b, message)
    s["b"] = {"answer": message, "right": right, "checked_by": how, "pasted": pasted}
    a_counts = s["a"]["right"] and s["label"] != "wrong"
    if right:
        points = 1 if a_counts else 0.5
        if a_counts and s["label"] == "unclear":
            card = {"kind": "passed", "heading": "Now you've put the reason into words." + points_phrase(s, points), "text": "You worked out why, then got a second new case right."}
        elif a_counts:
            card = {"kind": "passed", "heading": "Now you can explain it, not just recite it." + points_phrase(s, points), "text": "You turned the underlined phrase into a real reason."}
        else:
            card = {"kind": "half", "heading": "You worked it out." + points_phrase(s, points), "text": f"That's a case you hadn't seen. For the first case, the answer was: {case_a['answer']}."}
        return settle(s, points, card)
    points = 0.5 if a_counts else 0
    card = {
        "kind": "missed",
        "heading": ("Half a point for the first case. " if points and s["mode"] == "real" else "") + "Let's walk through it together.",
        "text": walkthrough(idea, s, case_a, case_b),
        "key_points": [
            {"text": "First case: " + case_a["answer"], "had": s["a"]["right"]},
            {"text": "Second case: " + case_b["answer"], "had": False},
        ],
    }
    return settle(s, points, card)


def state(s):
    return {
        "topic": s["items"][0]["topic"],
        "subtopic": s["subtopic"],
        "mode": s["mode"],
        "mode_label": MODES[s["mode"]],
        "notes_url": NOTES.get(s["subtopic"], ""),
        "ideas": [i["idea"] for i in s["items"]],
        "results": s["results"],
        "index": s["index"],
        "score": s["score"],
        "waiting": s["waiting"],
        "stage": s["stage"],
        "done": s["stage"] == "done",
    }


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
VOICE_PER_MINUTE = 10     # per IP address, each one is a speech-to-text call
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
    "Permissions-Policy": "camera=(), microphone=(self), geolocation=()",   # the mic only for our own page, for voice answers
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
    mode: Literal["practice", "real"] = "real"           # which segment a new test is in
    message: str = Field(min_length=1, max_length=2000)  # long enough to explain, short enough to stop abuse
    simpler: bool = False                                # the learner pressed "Make it simpler"
    pasted: bool = False                                 # the answer was pasted into the box, not typed


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
        s = new_test(body.subtopic, body.mode)
        row = db.table("tests").insert({"user_id": user["id"], "subtopic": body.subtopic, "mode": body.mode, "state": stored(s)}).execute().data[0]
        return {"test_id": row["id"], "messages": [intro(s), explain_card(s)], "state": state(s)}

    # Otherwise read where this learner was, answer, then write it back.
    s = load_test(db, body.test_id)  # 403 here if the test isn't theirs
    messages = respond(s, body.message, simpler=body.simpler, pasted=body.pasted)
    for attempt in s["new_attempts"]:
        db.table("attempts").insert({"test_id": body.test_id, **attempt}).execute()
    update = {"state": stored(s)}
    if s["new_attempts"] and s["stage"] == "done":
        update["score"] = percent(s)  # a percentage; only real-world tests colour the map
        update["completed_at"] = datetime.now(timezone.utc).isoformat()
    db.table("tests").update(update).eq("id", body.test_id).execute()
    return {"test_id": body.test_id, "messages": messages, "state": state(s)}


# The map: every topic and subtopic, with this learner's latest and best real-world score
# on each, and how many times they've practised it.
@app.get("/api/home")
def home_route(request: Request):
    user = current_user(request)
    limit("pages:" + user["id"], PAGES_PER_MINUTE, 60)
    finished = (
        learner_db(user["token"]).table("tests").select("subtopic, mode, score, completed_at")
        .eq("user_id", user["id"]).not_.is_("completed_at", "null")
        .order("completed_at").execute().data
    )
    scores = {}
    for row in finished:  # oldest first, so the last one seen is the latest
        mine = scores.setdefault(row["subtopic"], {"latest": None, "best": None, "tests": 0, "practised": 0})
        if row["mode"] == "practice":
            mine["practised"] += 1
        elif row["mode"] == "real":  # tests from before the two segments ("legacy") don't count
            mine["latest"] = round(row["score"])
            mine["best"] = max(mine["best"] or 0, round(row["score"]))
            mine["tests"] += 1
    return {"title": ROOT_TITLE, "email": user.get("email"), "topics": map_topics(scores)}


def map_topics(scores):
    topics = []
    for subtopic in SUBTOPICS:
        items = ideas_for(subtopic)
        if not topics or topics[-1]["title"] != items[0]["topic"]:
            topics.append({"title": items[0]["topic"], "subtopics": []})
        topics[-1]["subtopics"].append({
            "title": subtopic,
            "ideas": [i["idea"] for i in items],
            "notes_url": NOTES.get(subtopic, ""),
            **scores.get(subtopic, {"latest": None, "best": None, "tests": 0, "practised": 0}),
        })
    return topics


# --- Guests -------------------------------------------------------------------------
# A guest tries the app with no account. Nothing is written to Supabase: the test's
# state travels to the page and back with every message instead. The page can't be
# trusted, so the state is checked as strictly as any other package before it's used,
# and guests are rate-limited by IP address, since they reach the AI without logging in.
class CaseResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    answer: str = Field(max_length=2000)
    right: bool
    checked_by: Literal["code", "model"]
    pasted: bool = False


class GuestState(BaseModel):
    model_config = ConfigDict(extra="forbid")
    subtopic: str = Field(max_length=100)
    mode: Literal["practice", "real"]
    index: int = Field(ge=0, le=10)
    score: float = Field(ge=0, le=10)
    results: list[Literal[0, 0.5, 1]] = Field(max_length=10)
    waiting: bool
    stage: Literal["explain", "case_a", "follow_up", "case_b", "done"]
    explanation: str = Field(max_length=6000)
    label: Literal["own", "recited", "wrong", "unclear"] | None = None
    quote: str | None = Field(default=None, max_length=2000)
    simpler_used: bool
    asked_again: bool
    pasted: bool
    a: CaseResult | None = None
    follow_up: str | None = Field(default=None, max_length=1000)
    reply: str | None = Field(default=None, max_length=2000)
    b: CaseResult | None = None


class GuestChatIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    state: GuestState | None = None                       # none yet means "start one"
    subtopic: str = Field(max_length=100)
    mode: Literal["practice", "real"] = "practice"
    message: str = Field(min_length=1, max_length=2000)
    simpler: bool = False
    pasted: bool = False


GUEST_NOTE = "You're a guest, so nothing here is saved. Sign up to keep your scores on the map."


@app.get("/api/guest/home")
def guest_home_route(request: Request):
    limit("guest-pages:" + client_ip(request), PAGES_PER_MINUTE, 60)
    return {"title": ROOT_TITLE, "email": None, "guest": True, "topics": map_topics({})}


@app.post("/api/guest/chat")
def guest_chat_route(body: GuestChatIn, request: Request):
    limit("guest:" + client_ip(request), ANSWERS_PER_MINUTE, 60)  # 429 here if they're sending too fast

    if body.state is None:
        if body.subtopic not in SUBTOPICS:
            raise HTTPException(400, "Unknown subtopic: " + body.subtopic)
        s = new_test(body.subtopic, body.mode)
        first = intro(s)
        first["text"] += " " + GUEST_NOTE
        return {"guest_state": stored(s), "messages": [first, explain_card(s)], "state": state(s)}

    s = body.state.model_dump()
    if s["subtopic"] not in SUBTOPICS:
        raise HTTPException(400, "Unknown subtopic: " + s["subtopic"])
    s["items"] = ideas_for(s["subtopic"])
    s["new_attempts"] = []
    # The state must describe a real place in this set, or the flow could run off the end.
    if s["index"] > len(s["items"]) or (s["stage"] != "done" and s["index"] >= len(s["items"])):
        raise HTTPException(400, "That test can't be continued. Start it again from the map.")

    messages = respond(s, body.message, simpler=body.simpler, pasted=body.pasted)
    for m in messages:
        if m["kind"] == "done" and m["mode"] == "real":
            m["text"] = "That's your real-world score. As a guest it isn't saved: sign up to keep it on the map."
    return {"guest_state": stored(s), "messages": messages, "state": state(s)}


# --- Voice answers ---------------------------------------------------------------------
# The page records the learner speaking and sends the audio here. We pass it to Groq's
# speech-to-text with our key and send back only the words. The words go into the answer
# box, so the learner can read and fix them before sending: the transcript is never
# graded on its own. Guests can use it too, so the limit is per IP address.
VOICE_TYPES = {"audio/webm": "webm", "audio/ogg": "ogg", "audio/mp4": "mp4", "audio/mpeg": "mp3", "audio/wav": "wav", "audio/x-m4a": "m4a"}
VOICE_MAX_BYTES = 2_000_000   # about two minutes of speech; Vercel turns away anything over 4.5 MB anyway


def transcribe(audio, kind):
    key = os.environ.get("GROQ_API_KEY", "")
    if not key or key == "paste_your_key_here":
        raise HTTPException(500, "GROQ_API_KEY is missing. Paste your key into the .env file, then restart python3 app.py.")
    r = requests.post(
        "https://api.groq.com/openai/v1/audio/transcriptions",
        headers={"Authorization": "Bearer " + key},
        files={"file": ("answer." + VOICE_TYPES[kind], audio, kind)},
        data={"model": "whisper-large-v3-turbo", "language": "en", "response_format": "json", "temperature": "0"},
        timeout=60,
    )
    if r.status_code == 429:
        wait = r.headers.get("retry-after", "30")
        raise HTTPException(429, f"Voice is handling too many requests right now. Try again in {wait} seconds, or type your answer.", headers={"Retry-After": wait})
    if r.status_code != 200:
        print("Groq speech error", r.status_code, r.text[:300])
        raise HTTPException(502, f"The speech service replied {r.status_code}. Try again, or type your answer.")
    return r.json().get("text", "").strip()


@app.post("/api/transcribe")
async def transcribe_route(request: Request):
    limit("voice:" + client_ip(request), VOICE_PER_MINUTE, 60)
    kind = request.headers.get("content-type", "").split(";")[0].strip().lower()
    if kind not in VOICE_TYPES:
        raise HTTPException(400, "Send the recording as audio: webm, ogg, mp4, mp3, wav or m4a.")
    size = request.headers.get("content-length", "")
    if size.isdigit() and int(size) > VOICE_MAX_BYTES:
        raise HTTPException(413, "That recording is too long. Keep it under two minutes.")
    audio = await request.body()
    if len(audio) > VOICE_MAX_BYTES:
        raise HTTPException(413, "That recording is too long. Keep it under two minutes.")
    if len(audio) < 1000:
        raise HTTPException(400, "That recording was empty. Hold the mic a little longer.")
    # requests blocks while Groq works, so run it off the event loop.
    return {"text": await run_in_threadpool(transcribe, audio, kind)}


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
