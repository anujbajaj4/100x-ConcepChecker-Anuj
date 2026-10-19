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
