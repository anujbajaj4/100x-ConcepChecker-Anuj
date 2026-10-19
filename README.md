# 100x-ConcepChecker-Anuj

Concept Check finds where a learner stops understanding and starts reciting. Pick a subtopic from Module 2, answer scenario questions in chat, and get one follow-up on the sentence that sounds recited.

## Run it

1. Install the packages once:

   ```bash
   pip install -r requirements.txt
   ```

2. Copy `.env.example` to `.env` and paste your Groq key after `GROQ_API_KEY=`. `.env` never goes to GitHub.

3. Start everything:

   ```bash
   python3 app.py
   ```

4. Open http://127.0.0.1:8000, pick a subtopic and press Start test. Press Ctrl+C in the terminal to stop.

`python3 app.py` starts one server that serves both the page and the API, the same way `08-supabase-api` does in the c8-db repo. Inside, it uses uvicorn, the program that turns the FastAPI app into a running web server, and restarts it whenever you save a file.

## The two interfaces

- `public/index.html` is the page: a log-in screen, the mind map home, and the chat test. It holds no key, only the learner's own login token.
- `app.py` is the server. `POST /api/signup` and `POST /api/login` go through Supabase Auth and return a token. `GET /api/home` returns every topic and subtopic with the learner's latest and best score. `POST /api/chat` takes `{"session_id", "subtopic", "message"}` and returns `{"messages", "state"}`: the reply cards to show, and the quiz state the sidebar draws (results so far, score, current question, notes link). Pydantic rejects any other package with a 422. The Groq key lives only here, read from `.env` locally and from Vercel's environment variables in production.

| Dial | `POST /api/chat` |
|---|---|
| Address | `/api/chat`, same origin as the page |
| Action | POST |
| Key | None from the browser. The server adds the Groq key itself |
| Package | `{"session_id", "subtopic", "message"}` in, `{"messages", "state"}` out |

## Storage

Supabase holds two tables, created by `schema.sql`: `tests` (one row each time a learner presses Test, with where they are and the final score) and `attempts` (one row per question, with the points, the recited sentence and each follow-up's target key point). Learners and their hashed passwords live in Supabase Auth. `rest_check.py` exercises both tables with plain requests.

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
- `app.py`: the FastAPI routes, the Groq call and the scoring. Run it with `python3 app.py`.
- `.env.example`: the variable names to copy into your own `.env`.
- `prompts.py`: the grader, follow-up and explanation prompts.
- `golden_pairs.json`: 54 questions with key points and pass rules across 12 subtopics.

Quiz state is kept in the server's memory for now. It resets when the server restarts, and on Vercel a request can land on a fresh machine, so a quiz may lose its place there. Saving it to Supabase is next.

## Security

| Layer | What the app does |
|---|---|
| Keys | The browser holds no key. `app.py` uses only Supabase's publishable key plus each learner's own token, and the Groq key. The secret key is for the admin scripts and never goes to Vercel. `.env` is git-ignored. |
| Who are you | Every `/api/chat` and `/api/home` request checks the learner's token with Supabase: no token or an expired one gets 401. Opening a test that isn't yours gets 403. |
| Rows | `security.sql` turns on row level security policies: a learner can read, start and update only their own tests and attempts, and nobody can delete. `python3 rls_check.py` proves it with two practice learners. The login token sits in an HttpOnly cookie the page can't read, and quiz state lives on the server. |
| Packages | Pydantic checks every package before any route code runs: the right fields and types, length limits (a message is 1 to 2,000 characters), and no extra fields. Anything else gets 400 with the rule it broke. `security.sql` adds the same kind of rules inside Postgres, such as points being 0, 0.5 or 1. |

### If a key leaks

1. Revoke it first: console.groq.com/keys for Groq, or Project Settings → API Keys in Supabase.
2. Create a new one and put it in `.env` and in Vercel's environment variables.
3. Anything still using the old key now fails with 401, which shows you what you missed.
4. Delete the message or screenshot last. Once a key has been seen, assume it was copied.
