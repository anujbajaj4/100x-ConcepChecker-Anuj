import json
import os
import requests
import gradio as gr

from prompts import FOLLOW_UP_PROMPT, GRADER_PROMPT

PAIRS = json.load(open("golden_pairs.json"))["pairs"]
SUBTOPICS = list(dict.fromkeys(p["subtopic"] for p in PAIRS))
MAX_FOLLOW_UPS = 2

# One quiz per browser tab, kept in memory. It resets when the app restarts.
# Saving scores to Supabase comes next.
SESSIONS = {}


def ask_groq(system, user):
    r = requests.post(
        "https://api.groq.com/openai/v1/chat/completions",
        headers={"Authorization": "Bearer " + os.environ["GROQ_API_KEY"]},
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
        raise gr.Error(f"Groq replied {r.status_code}: {r.text[:200]}")
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


# Code decides: which pass-rule key points are still missing.
def missing(pair, met):
    v = pair["verifier"]
    gaps = [k for k in v["required"] if k not in met]
    if v["any_of"] and not met & set(v["any_of"]):
        gaps.append(v["any_of"][0])
    return [k for k in pair["key_points"] if k["id"] in gaps]


def ask(s):
    pair = s["pairs"][s["index"]]
    return f"**Question {s['index'] + 1} of {len(s['pairs'])}: {pair['concept']}**\n\n{pair['question']}"


def finish(s):
    total = len(s["pairs"])
    percent = round(100 * s["score"] / total)
    if percent >= 80:
        label = "Mastered"
    elif percent >= 50:
        label = "Getting there"
    else:
        label = "Needs work"
    return (
        f"**Set done: {s['score']:g} out of {total} ({percent}%). {label}.**\n\n"
        "Send any message to retake this set, or pick another subtopic."
    )


def next_question(s, points, reply):
    s["score"] += points
    s["index"] += 1
    s["turns"] = []
    s["met"] = set()
    s["follow_ups"] = 0
    if s["index"] < len(s["pairs"]):
        return reply + "\n\n" + ask(s)
    return reply + "\n\n" + finish(s)


def respond(message, history, subtopic, request: gr.Request):
    s = SESSIONS.get(request.session_hash)

    # Start a new set: first message, a new subtopic, or the last set is done.
    if s is None or s["subtopic"] != subtopic or s["index"] >= len(s["pairs"]):
        s = {
            "subtopic": subtopic,
            "pairs": [p for p in PAIRS if p["subtopic"] == subtopic],
            "index": 0,
            "score": 0,
            "turns": [],
            "met": set(),
            "follow_ups": 0,
        }
        SESSIONS[request.session_hash] = s
        return (
            f"**{subtopic}**: {len(s['pairs'])} questions. "
            "Answer in your own words, the way you'd explain it to a classmate.\n\n" + ask(s)
        )

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
            return next_question(s, 1, "**Passed.** " + result.get("note", ""))
        return next_question(s, 0.5, "**Passed after a follow-up, half a point.** " + result.get("note", ""))

    if s["follow_ups"] < MAX_FOLLOW_UPS:
        target = gaps[0] if gaps else pair["key_points"][0]
        follow_up = write_follow_up(pair, s, target)
        s["follow_ups"] += 1
        s["turns"].append((follow_up, ""))
        if s["follow_ups"] == 1 and result.get("recited_sentence"):
            lead = f"> {result['recited_sentence']}\n\nThat sentence sounds recited. "
        elif s["met"]:
            lead = "You're partway there. "
        else:
            lead = "Let's work it out a step at a time. "
        return lead + "Think about this:\n\n**" + follow_up + "**"

    # Out of nudges: show the piece that was missing and move on.
    pieces = gaps or pair["key_points"][:1]
    reply = "**Not yet, 0 points.** Here's the piece you were missing:\n" + "\n".join("- " + k["text"] for k in pieces)
    return next_question(s, 0, reply)


demo = gr.ChatInterface(
    fn=respond,
    title="Concept Check",
    description="Pick a subtopic, then send any message to start.",
    textbox=gr.Textbox(placeholder="Explain it the way you'd tell a classmate"),
    additional_inputs=[gr.Dropdown(SUBTOPICS, value=SUBTOPICS[0], label="Subtopic")],
    additional_inputs_accordion=gr.Accordion("Subtopic", open=True),
)

if __name__ == "__main__":
    demo.launch()
