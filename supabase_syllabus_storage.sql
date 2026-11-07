-- Run once per Supabase project in the SQL editor: creates the private bucket for course
-- syllabi and the policies that let the agent (anon key) upload, list and read them.
-- Files are stored at <Request_ID>/old_course.<ext> and <Request_ID>/new_course.<ext>.
-- DEVELOPMENT POLICIES: they are open to the anon role. Restrict them (for example to
-- authenticated staff) before the final demonstration.

insert into storage.buckets (id, name, public, file_size_limit, allowed_mime_types)
values ('course-syllabi', 'course-syllabi', false, 10485760,
        array['application/pdf',
              'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
              'text/plain'])
on conflict (id) do update
  set file_size_limit = excluded.file_size_limit, allowed_mime_types = excluded.allowed_mime_types;

drop policy if exists "syllabi_insert" on storage.objects;
drop policy if exists "syllabi_select" on storage.objects;
drop policy if exists "syllabi_update" on storage.objects;

create policy "syllabi_insert" on storage.objects for insert to anon, authenticated
  with check (bucket_id = 'course-syllabi');
create policy "syllabi_select" on storage.objects for select to anon, authenticated
  using (bucket_id = 'course-syllabi');
create policy "syllabi_update" on storage.objects for update to anon, authenticated
  using (bucket_id = 'course-syllabi') with check (bucket_id = 'course-syllabi');
