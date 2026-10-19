# 100x-ConcepChecker-Anuj

Concept Check finds where a learner stops understanding and starts reciting. Pick a subtopic from Module 2, answer scenario questions in chat, and get one follow-up on the sentence that sounds recited.

## Run it

```bash
pip install -r requirements.txt
export GROQ_API_KEY=gsk_your_key_here
uvicorn api.index:app --reload
```

Then open http://127.0.0.1:8000, pick a subtopic and press Start test.

## The two interfaces

- `public/index.html` is the page: a dropdown, a chat log, a text box, a button and one `fetch` to `/api/chat`. It holds no key.
- `api/index.py` is the server. `GET /api/subtopics` lists the subtopics. `POST /api/chat` takes `{"session_id", "subtopic", "message"}` and returns `{"messages", "state"}`: the reply cards to show, and the quiz state the sidebar draws (results so far, score, current question, notes link). Pydantic rejects any other package with a 422. The Groq key lives only here, in an environment variable.

| Dial | `POST /api/chat` |
|---|---|
| Address | `/api/chat`, same origin as the page |
| Action | POST |
| Key | None from the browser. The server adds the Groq key itself |
| Package | `{"session_id", "subtopic", "message"}` in, `{"messages", "state"}` out |

## How a set works

1. The app asks the subtopic's questions from `golden_pairs.json`, one at a time.
2. Each answer goes to `openai/gpt-oss-120b` on Groq with the grader prompt in `prompts.py`.
3. If the answer misses a key point the pass rule needs, code picks that missing point and the model writes a stepping-stone question aimed only at it, building on what the learner got right. Up to two of these, each more concrete than the last. Credit carries across replies, so a point shown once stays shown.
4. Pass on the first try: 1 point. Pass after a follow-up: half a point. Otherwise 0, and the app explains the full answer from the answer key, marking the parts you had. After each question, say "next" to move on.
5. At the end you get the set score and a label: Mastered (80% and up), Getting there (50 to 79%), Needs work (under 50%).

## Who decides what

| Code decides | AI decides |
|---|---|
| Which question comes next | Which key points the answer shows in the learner's own words |
| Pass or fail, from the key points the model reports and the pass rule in `golden_pairs.json` | The recited sentence, quoted |
| The score | The wording of each follow-up (code picks what it aims at) |

The model never decides on its own that a learner understands. The answer key is written before any session.

## Files

- `public/index.html`: the chat page.
- `api/index.py`: the FastAPI routes, the Groq call and the scoring.
- `prompts.py`: the grader, follow-up and explanation prompts.
- `golden_pairs.json`: 54 questions with key points and pass rules across 12 subtopics.

Quiz state is kept in the server's memory for now. It resets when the server restarts, and on Vercel a request can land on a fresh machine, so a quiz may lose its place there. Saving it to Supabase is next.
