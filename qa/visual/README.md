# On-demand admin visual QA

Run `.github/workflows/visual-qa.yml` manually with a `run_label` of 6–64 letters,
digits or dashes, and a JSON `plan`. It uses a disposable SQLite database and a
one-run admin; it requires no repository secrets. Evidence is uploaded as an
artifact, including when setup or capture fails.

The runner and its three libraries are exact copies of
`~/clawd/tools/visual-qa/{ci-template/run.mjs,lib/{plan,capture,util}.mjs}`.
Make runner changes upstream and copy them here again.

The supplied validator requires `repo` and integer `pr` metadata even for a local
run (use `0` when there is no PR). For example:

```json
{"name":"admin-review","repo":"surp-hovhannes/bahk","pr":0,"scenarios":[{"name":"admin-index","path":"/admin/"},{"name":"fasts","path":"/admin/hub/fast/"},{"name":"devotionals","path":"/admin/hub/devotional/"},{"name":"events","path":"/admin/events/event/"},{"name":"engagement","path":"/admin/events/event/analytics/"},{"name":"app-analytics","path":"/admin/events/event/analytics/app/"}]}
```

`python manage.py seed_visual_qa` is for disposable QA databases only. It creates
Visual QA Church, a seven-day fast, a devotional set/video/devotional, a reading
with local passage text, two inactive prompts, reading/feast contexts, and three
non-staff users with unusable passwords, memberships and 24 events. No media files
are uploaded. Natural-key database upserts bypass model saves and signals so no
engines, tasks, moderation, email, push, Bible API or storage calls run. Running it
again restores fixture values without duplicating them; it does not create an admin.

Dates use the fixed anchor **2026-10-19 12:00 UTC**. Analytics defaults to rolling
windows, so historical fixtures eventually disappear from recent charts; they
remain in event lists. The fast and devotional change forms and the reading
comparison and feast regeneration forms can be opened from their lists. Prompt
comparison POSTs and regeneration submissions invoke real services; capture their
forms without submitting them.

For local smoke, use `DEBUG=true CI=true` and set `DATABASE_URL` to an absolute
throwaway SQLite path before migrating, seeding, creating a temporary superuser,
and starting Django. Never use the repository's database. In this directory run
`node run.mjs` with `VQA_PLAN` (including the metadata above), `VQA_BASE_URL`,
`VQA_USER`, and `VQA_PASSWORD` in the environment. Install Playwright only if needed
(`npm install --no-save playwright@1.64.1`). Check `qa-results/manifest.json` for
`ok` results and remove the output afterwards. The runner exits nonzero only on
setup failure, so inspect the manifest for individual scenario failures too.
