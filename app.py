import json
import os
from pathlib import Path

import requests
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

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

# One quiz per browser tab, kept in memory. It resets when the server restarts,
# and on Vercel each request may land on a fresh machine. Supabase fixes both.
SESSIONS = {}


def ask_groq(system, user):
    key = os.environ.get("GROQ_API_KEY", "")
    if not key or key == "paste_your_key_here":
        raise HTTPException(500, "GROQ_API_KEY is missing. Paste your key into the .env file, then restart python app.py.")
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


def conversation(s):
    lines = ["Learner's first answer: " + s["turns"][0][1]]
    for follow_up, reply in s["turns"][1:]:
        lines.append("Follow-up: " + follow_up + "\nLearner's reply: " + reply)
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
        "text": "Try the set again to beat your score, or pick another subtopic.",
    }


# Record the result, then either wait for "next" or close the set.
def settle(s, points, card):
    s["results"].append(points)
    s["score"] += points
    s["index"] += 1
    s["turns"] = []
    s["met"] = set()
    s["follow_ups"] = 0
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


def respond(session_id, subtopic, message):
    s = SESSIONS.get(session_id)

    # Start a new set: first message, a new subtopic, or the last set is done.
    if s is None or s["subtopic"] != subtopic or s["index"] >= len(s["pairs"]):
        s = {
            "subtopic": subtopic,
            "pairs": [p for p in PAIRS if p["subtopic"] == subtopic],
            "index": 0,
            "score": 0,
            "results": [],
            "turns": [],
            "met": set(),
            "follow_ups": 0,
            "waiting": False,
        }
        SESSIONS[session_id] = s
        intro = {
            "kind": "intro",
            "text": f"{len(s['pairs'])} questions on **{subtopic}**. Answer in your own words, the way you'd explain it to a classmate. There's no need for textbook phrasing.",
        }
        return [intro, question(s)], s

    # A question was just settled: wait until the learner asks for the next one.
    if s["waiting"]:
        if "next" in message.lower():
            s["waiting"] = False
            return [question(s)], s
        return [{"kind": "next", "text": f"Say **next** when you're ready for question {s['index'] + 1}."}], s

    pair = s["pairs"][s["index"]]
    if s["turns"]:
        s["turns"][-1] = (s["turns"][-1][0], message)
    else:
        s["turns"].append((pair["question"], message))

    result = grade(pair, s)
    # Credit carries across replies: a point shown once stays shown.
    s["met"] |= set(result.get("key_points_met", []))
    gaps = missing(pair, s["met"])

    if not gaps and not result.get("fail_if_hit"):
        if s["follow_ups"] == 0:
            card = {"kind": "passed", "heading": "Nicely reasoned. 1 point.", "text": result.get("note", "")}
            return settle(s, 1, card), s
        card = {"kind": "half", "heading": "You got there. Half a point for the nudge.", "text": result.get("note", "")}
        return settle(s, 0.5, card), s

    if s["follow_ups"] < MAX_FOLLOW_UPS:
        target = gaps[0] if gaps else pair["key_points"][0]
        follow_up = write_follow_up(pair, s, target)
        s["follow_ups"] += 1
        s["turns"].append((follow_up, ""))
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
        return [{"kind": "follow_up", "heading": heading, "text": text, "question": follow_up, "quote": quote}], s

    # Out of nudges: walk through the full answer, then show the whole answer key.
    card = {
        "kind": "missed",
        "heading": "Let's walk through it together.",
        "text": explain(pair, s),
        "key_points": [{"text": k["text"], "had": k["id"] in s["met"]} for k in pair["key_points"]],
    }
    return settle(s, 0, card), s


app = FastAPI()


# The traffic police: a chat package must carry these three, as text.
class ChatIn(BaseModel):
    session_id: str
    subtopic: str
    message: str


@app.get("/api/subtopics")
def subtopics_route():
    return {"subtopics": SUBTOPICS}


@app.post("/api/chat")
def chat_route(body: ChatIn):
    if body.subtopic not in SUBTOPICS:
        raise HTTPException(400, "Unknown subtopic: " + body.subtopic)
    messages, s = respond(body.session_id, body.subtopic, body.message)
    return {"messages": messages, "state": state(s)}


# Locally, serve the page from this same server, so the page and the API share
# one address, exactly like on Vercel. On Vercel, its CDN serves public/ itself.
# This mount comes last so the /api routes above are matched first.
PUBLIC = ROOT / "public"
if PUBLIC.is_dir():
    app.mount("/", StaticFiles(directory=PUBLIC, html=True), name="public")


# python app.py starts everything: the API and the page, at http://127.0.0.1:8000
# uvicorn is the program that turns the FastAPI app above into a running web server.
# reload=True restarts it whenever you save a file.
if __name__ == "__main__":
    import uvicorn

    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("app:app", app_dir=str(ROOT), port=port, reload=True, reload_dirs=[str(ROOT)])
