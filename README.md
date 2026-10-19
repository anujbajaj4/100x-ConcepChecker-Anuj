# 100x-ConcepChecker-Anuj

Concept Check finds where a learner stops understanding and starts reciting. Pick a subtopic from Module 2, answer scenario questions in chat, and get one follow-up on the sentence that sounds recited.

## Live

https://concept-check-anuj.vercel.app

## Deploy

The project is linked to Vercel as `concept-check-anuj`, with the FastAPI preset set in `vercel.json`.

1. Production needs three environment variables in Vercel: `GROQ_API_KEY`, `SUPABASE_URL` and `SUPABASE_PUBLISHABLE_KEY`. The secret Supabase key never goes to Vercel.
2. Deploy with `vercel --prod`.

`.vercelignore` keeps `.env` and the admin scripts on your laptop. On Vercel, `public/` is served by its CDN, and every `/api` request goes to `app.py`.

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
- `app.py` is the server. `POST /api/signup` and `POST /api/login` go through Supabase Auth and return a token. `GET /api/guest/home` and `POST /api/guest/chat` let a guest try it with no account: nothing reaches Supabase, the test's state travels with each message and is validated strictly, and guests are rate-limited by IP. `POST /api/transcribe` takes a voice recording (webm, ogg, mp4, mp3, wav or m4a, up to 2 MB) and returns `{"text"}` from Groq's `whisper-large-v3-turbo`; the words land in the answer box for the learner to check before sending, so a transcript is never graded unseen. `GET /api/home` returns every topic and subtopic with the learner's latest and best real-world score and how often they've practised it. `POST /api/chat` takes `{"test_id", "subtopic", "mode", "message", "simpler", "pasted"}` and returns `{"messages", "state"}`: the reply cards to show, and the state the sidebar draws. Pydantic rejects any other package with a 400. The Groq key lives only here, read from `.env` locally and from Vercel's environment variables in production.

| Dial | `POST /api/chat` |
|---|---|
| Address | `/api/chat`, same origin as the page |
| Action | POST |
| Key | None from the browser. The server adds the Groq key itself |
| Package | `{"test_id", "subtopic", "mode", "message", "simpler", "pasted"}` in, `{"messages", "state"}` out |

## Storage

Supabase holds two tables, created by `schema.sql`: `tests` (one row each time a learner starts Practice or the Real-world test, with its `mode`, where they are and the final score) and `attempts` (one row per idea, with the points, the recited sentence, the explanation's label, both cases, the follow-up and its reply, and whether answers were pasted). Learners and their hashed passwords live in Supabase Auth. `rest_check.py` exercises both tables with plain requests.

## How a set works

Each subtopic has two ideas, and every idea runs the same loop, taken from the hackathon brief:

1. **Explain it** in your own words. Not scored. The model labels the explanation *own reasoning*, *recited*, *wrong* or *unclear*, and quotes the sentence that shows it. "Make it simpler" shows a gentler version of the prompt, written in advance. A pasted answer is recorded.
2. **Case A**: a new situation with one short answer written in advance. Code checks it against the case's accept and reject words.
3. If case A is wrong, or the explanation was recited or wrong: **one follow-up** aimed at the quoted sentence, then **case B**, a different situation chosen so the common wrong idea gets it wrong.

| Explanation | Case A | Case B | Points |
|---|---|---|---|
| Own reasoning | right | (not asked) | 1 |
| Recited | right | right / wrong | 1 / 0.5 |
| Any | wrong | right / wrong | 0.5 / 0 |
| Wrong | right (by luck, so it doesn't count) | right / wrong | 0.5 / 0 |

There are two segments:

- **Practice** uses cases set in Aarav's world. It's never scored, so learners who are still catching up can repeat it as often as they like.
- **Real-world test** asks the same ideas inside apps people use every day: UPI, Swiggy, IRCTC, WhatsApp, banks. Only this segment's score colours the map: 80% and up is Mastered, 50 to 79% is Getting there, under 50% is Needs work.

## Who decides what

| Code decides | AI decides |
|---|---|
| The ideas, the cases and their answers, all written in `golden_pairs.json` before any session | Whether an explanation is own reasoning, recited or wrong, and which sentence shows it |
| Whether a case answer is right, from its accept and reject words | The wording of the one follow-up, aimed at that sentence |
| The points and the score | Only when an answer hits neither word list: whether it means the same as the written answer |

The model never decides on its own that a learner understands. A pass is always a new case answered right.

## Files

- `public/index.html` and `public/app.js`: the page and its code.
- `app.py`: the FastAPI routes, the Groq call and the scoring. Run it with `python3 app.py`.
- `.env.example`: the variable names to copy into your own `.env`.
- `schema.sql`, `security.sql` and `segments.sql`: the tables, the row level security policies and column rules, then the practice/real-world `mode` column. Run them in that order in the Supabase SQL Editor.
- `rest_check.py` and `rls_check.py`: plain-request checks of the tables and of row level security.
- `vercel.json`: the security headers for the page on Vercel.
- `prompts.py`: the four narrow prompts: label the explanation, write the follow-up, compare a short answer, walk through the answers.
- `golden_pairs.json`: 24 ideas, 2 per subtopic. Each has an explain prompt and a simpler version, what understanding looks like, the common wrong idea, stock phrases, a follow-up seed, 2 practice cases and 2 real-world cases.

Quiz state is kept in the server's memory for now. It resets when the server restarts, and on Vercel a request can land on a fresh machine, so a quiz may lose its place there. Saving it to Supabase is next.

## Security

| Layer | What the app does |
|---|---|
| Keys | The browser holds no key. `app.py` uses only Supabase's publishable key plus each learner's own token, and the Groq key. The secret key is for the admin scripts and never goes to Vercel. `.env` is git-ignored. |
| Who are you | Every `/api/chat` and `/api/home` request checks the learner's token with Supabase: no token or an expired one gets 401. Opening a test that isn't yours gets 403. |
| Rows | `security.sql` turns on row level security policies: a learner can read, start and update only their own tests and attempts, and nobody can delete. `python3 rls_check.py` proves it with two practice learners. The login token sits in an HttpOnly cookie the page can't read, and quiz state lives on the server. |
| Packages | Pydantic checks every package before any route code runs: the right fields and types, length limits (a message is 1 to 2,000 characters), and no extra fields. Anything else gets 400 with the rule it broke. `security.sql` adds the same kind of rules inside Postgres, such as points being 0, 0.5 or 1. |
| Limits | Too many requests get 429 with a Retry-After: 10 log-ins a minute per IP address, 15 answers a minute and 60 map loads a minute per learner, 10 voice recordings a minute per IP address. There's no daily cap per learner; if Groq's shared 1,000 requests a day run out, its own 429 is passed on with its wait time. The per-minute counters live in each server's memory. |
| Isolation | The browser only ever calls our own `/api`. Groq and Supabase are reached from the server. There's no CORS, so other websites can't call the API from a visitor's browser. Every response carries security headers (a Content Security Policy that only runs our own `app.js`, no framing, HTTPS only, no sniffing, and a Permissions-Policy that lets only our own page ask for the microphone), set in `app.py` and, for the CDN on Vercel, in `vercel.json`. API replies are never cached, and `/docs` is switched off on Vercel. |

### If a key leaks

1. Revoke it first: console.groq.com/keys for Groq, or Project Settings → API Keys in Supabase.
2. Create a new one and put it in `.env` and in Vercel's environment variables.
3. Anything still using the old key now fails with 401, which shows you what you missed.
4. Delete the message or screenshot last. Once a key has been seen, assume it was copied.
