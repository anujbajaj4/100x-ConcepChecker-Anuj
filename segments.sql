-- Two segments: Practice and the Real-world test. Run once in Supabase after schema.sql
-- and security.sql: SQL Editor, New query, paste, Run. Running it again is safe.

-- Which segment each test belongs to. Tests taken before this change are marked
-- 'legacy': they were graded the old way, so the map ignores their scores.
alter table tests add column if not exists mode text not null default 'legacy';
alter table tests alter column mode set default 'real';

alter table tests drop constraint if exists mode_allowed;
alter table tests add constraint mode_allowed check (mode in ('practice', 'real', 'legacy'));

-- question_id now holds an idea id such as be-process-1, which can be a little longer.
alter table attempts drop constraint if exists question_id_length;
alter table attempts add constraint question_id_length check (char_length(question_id) between 1 and 60);
