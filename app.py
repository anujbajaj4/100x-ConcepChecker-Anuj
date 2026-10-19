import json
import os
import requests
import gradio as gr

from prompts import GRADER_PROMPT

PAIRS = json.load(open("golden_pairs.json"))["pairs"]
SUBTOPICS = list(dict.fromkeys(p["subtopic"] for p in PAIRS))

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


# AI decides: which key points the answer shows, the recited sentence, the follow-up.
def grade(pair, answer):
    v = pair["verifier"]
    key_points = "\n".join(k["id"] + ": " + k["text"] for k in pair["key_points"])
    user = (
        "Question:\n" + pair["question"]
        + "\n\nKey points:\n" + key_points
        + "\n\nRecitation phrases:\n" + ", ".join(v["recitation_flags"])
        + "\n\nMisconceptions:\n" + "; ".join(v["fail_if"])
        + "\n\nLearner's answer:\n" + answer
    )
    return ask_groq(GRADER_PROMPT, user)


# Code decides: pass or fail, against the answer key written before the session.
def passed(pair, result):
    v = pair["verifier"]
    met = set(result.get("key_points_met", []))
    if result.get("fail_if_hit"):
        return False
    if not set(v["required"]) <= met:
        return False
    return not v["any_of"] or bool(met & set(v["any_of"]))


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


def respond(message, history, subtopic, request: gr.Request):
    s = SESSIONS.get(request.session_hash)

    # Start a new set: first message, a new subtopic, or the last set is done.
    if s is None or s["subtopic"] != subtopic or s["index"] >= len(s["pairs"]):
        s = {
            "subtopic": subtopic,
            "pairs": [p for p in PAIRS if p["subtopic"] == subtopic],
            "index": 0,
            "score": 0,
            "first_answer": None,
            "follow_up": None,
        }
        SESSIONS[request.session_hash] = s
        return (
            f"**{subtopic}**: {len(s['pairs'])} questions. "
            "Answer in your own words, the way you'd explain it to a classmate.\n\n" + ask(s)
        )

    pair = s["pairs"][s["index"]]
    if s["first_answer"] is None:
        answer = message
    else:
        answer = (
            "First answer: " + s["first_answer"]
            + "\nFollow-up asked: " + s["follow_up"]
            + "\nReply to the follow-up: " + message
        )
    result = grade(pair, answer)

    if passed(pair, result):
        points = 1 if s["first_answer"] is None else 0.5
        heading = "Passed." if points == 1 else "Passed after a follow-up, half a point."
        reply = f"**{heading}** {result.get('note', '')}"
    elif s["first_answer"] is None:
        # First miss: point at the recited sentence and ask one follow-up.
        s["first_answer"] = message
        s["follow_up"] = result.get("follow_up", "What would break in this scenario?")
        if result.get("recited_sentence"):
            reply = f"> {result['recited_sentence']}\n\nThat sentence sounds recited. "
        else:
            reply = "Not quite there yet. "
        return reply + "One question about it:\n\n**" + s["follow_up"] + "**"
    else:
        # Second miss: show what a full answer covers and move on.
        points = 0
        met = set(result.get("key_points_met", []))
        missed = [k["text"] for k in pair["key_points"] if k["id"] not in met]
        reply = "**Not yet.** A full answer would also cover:\n" + "\n".join("- " + m for m in missed)

    s["score"] += points
    s["index"] += 1
    s["first_answer"] = None
    s["follow_up"] = None
    if s["index"] < len(s["pairs"]):
        return reply + "\n\n" + ask(s)
    return reply + "\n\n" + finish(s)


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
