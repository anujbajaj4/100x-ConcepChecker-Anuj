-- Concept Check schema. Run once in Supabase: SQL Editor, New query, paste, Run.
--
-- Learners are not a table here. Supabase Auth keeps them, with their email and a
-- hashed password, in its own auth.users table. We never store a password ourselves.

-- One row each time a learner presses Start test.
-- A retake is a new row, so old scores stay: history is a row, not a cell.
create table tests (
  id bigint generated always as identity primary key,
  user_id uuid not null references auth.users(id),
  subtopic text not null,
  mode text not null default 'real',   -- 'practice' (never scored) or 'real' (colours the map); added by segments.sql
  state jsonb not null default '{}',   -- where the learner is right now in this test
  score real,                          -- filled in when the test is complete
  completed_at timestamptz,
  created_at timestamptz default now()
);

-- One row per question answered inside a test.
create table attempts (
  id bigint generated always as identity primary key,
  test_id bigint not null references tests(id),
  question_id text not null,           -- an idea id from golden_pairs.json, such as be-process-1
  points real not null,                -- 1, 0.5 or 0
  recited_sentence text,               -- the sentence that sounded memorised, if any
  turns jsonb not null,                -- the explanation, its label, both cases and the follow-up
  created_at timestamptz default now()
);

-- Lock both tables. With no policies yet, only the server, using the secret key,
-- can read or write rows. The policies that let each learner see their own rows
-- come in the Row Level Security step.
alter table tests enable row level security;
alter table attempts enable row level security;
