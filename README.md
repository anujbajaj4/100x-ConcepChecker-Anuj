# 100x-ConcepChecker-Anuj

Concept Check finds where a learner stops understanding and starts reciting. Pick a subtopic from Module 2, answer scenario questions in chat, and get one follow-up on the sentence that sounds recited.

## Run it

```bash
pip install -r requirements.txt
export GROQ_API_KEY=gsk_your_key_here
python app.py
```

Then open the local link Gradio prints, pick a subtopic and send any message to start.

## How a set works

1. The app asks the subtopic's questions from `golden_pairs.json`, one at a time.
2. Each answer goes to `openai/gpt-oss-120b` on Groq with the grader prompt in `prompts.py`.
3. Pass on the first try: 1 point. Pass after one follow-up: half a point. Otherwise 0, and the app shows what a full answer covers.
4. At the end you get the set score and a label: Mastered (80% and up), Getting there (50 to 79%), Needs work (under 50%).

## Who decides what

| Code decides | AI decides |
|---|---|
| Which question comes next | Which key points the answer shows in the learner's own words |
| Pass or fail, from the key points the model reports and the pass rule in `golden_pairs.json` | The recited sentence, quoted |
| The score | The one follow-up question |

The model never decides on its own that a learner understands. The answer key is written before any session.

## Files

- `app.py`: the Gradio chat, the Groq call and the scoring.
- `prompts.py`: the grader prompt.
- `golden_pairs.json`: 54 questions with key points and pass rules across 12 subtopics.

Scores are kept in memory for now and reset when the app restarts. Saving them to Supabase is next.
