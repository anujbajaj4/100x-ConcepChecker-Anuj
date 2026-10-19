-- Concept Check security rules. Run once in Supabase after schema.sql:
-- SQL Editor, New query, paste, Run. Running it again is safe: each rule is dropped first.

-- Row level security ----------------------------------------------------------------
-- schema.sql locked both tables. These policies open them one row at a time:
-- a logged-in learner can only see and change rows that belong to them.
-- auth.uid() is the user id inside the token the request carries. A request with no
-- token, or someone else's, matches no rows, so an injected or forged query gets nothing.
-- There is no delete policy, so no learner can delete anything, not even their own rows.

drop policy if exists "Learners read their own tests" on tests;
create policy "Learners read their own tests" on tests
  for select to authenticated
  using ((select auth.uid()) = user_id);

drop policy if exists "Learners start their own tests" on tests;
create policy "Learners start their own tests" on tests
  for insert to authenticated
  with check ((select auth.uid()) = user_id);

drop policy if exists "Learners update their own tests" on tests;
create policy "Learners update their own tests" on tests
  for update to authenticated
  using ((select auth.uid()) = user_id)
  with check ((select auth.uid()) = user_id);

-- An attempt has no user_id of its own. It belongs to whoever owns its test,
-- so these follow the test_id pointer back to tests.
drop policy if exists "Learners read attempts in their own tests" on attempts;
create policy "Learners read attempts in their own tests" on attempts
  for select to authenticated
  using (exists (select 1 from tests where tests.id = attempts.test_id and tests.user_id = (select auth.uid())));

drop policy if exists "Learners add attempts to their own tests" on attempts;
create policy "Learners add attempts to their own tests" on attempts
  for insert to authenticated
  with check (exists (select 1 from tests where tests.id = attempts.test_id and tests.user_id = (select auth.uid())));

-- Column rules ----------------------------------------------------------------------
-- The second traffic police, in the database itself. Even if a bad value got past the
-- server, Postgres refuses to store it.

alter table attempts drop constraint if exists points_allowed;
alter table attempts add constraint points_allowed check (points in (0, 0.5, 1));

alter table tests drop constraint if exists score_is_a_percentage;
alter table tests add constraint score_is_a_percentage check (score is null or score between 0 and 100);

alter table tests drop constraint if exists subtopic_length;
alter table tests add constraint subtopic_length check (char_length(subtopic) between 1 and 100);

alter table attempts drop constraint if exists question_id_length;
alter table attempts add constraint question_id_length check (char_length(question_id) between 1 and 60);
