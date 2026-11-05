# Rebuild the PhotoPrism and salamander MariaDB data directories from their dumps

## What this is about

Both MariaDB servers run `mariadb:10.11.19` on data directories that newer MariaDB
majors last upgraded. MariaDB does not support downgrades, so both run in an untested
state. Found 2026-10-12, in the backups project, while weighing whether their
volume copies were worth keeping.

## What is true -- verified 2026-10-12

| | photoprism | salamander |
|---|---|---|
| Running | 10.11.19 | 10.11.19 |
| `mariadb_upgrade_info` (the last version to run `mariadb-upgrade` on it) | 11.0.2 | 12.2.2 |
| `system_mysql_backup_*` files the image's auto-upgrade left | 10.10.2 (2023-03), 10.11.4 (2024-02), unknown | 10.10.2, then every release from 11.0.2 to 12.1.2 (2023-10 to 2026-02), unknown (2026-08-07) |
| Data directory | 455 MB | 698 MB |

**Both log at every start:** `[ERROR] InnoDB: The change buffer is corrupted or has
been removed on upgrade to MariaDB 11.0 or later`. That is 10.11 reading a data
directory that 11.0 or later changed.

**How it happened.** Until commit `8ad6df4a` (2026-08-07), salamander's database
image was an untagged `mariadb`, so every restart pulled the newest major: it climbed
to 12.2.2. That commit pinned it to 10.11.18, a downgrade from 12.2. When photoprism's
server ran 11.0.2, and when it went back to 10.x, has not been traced; the 10.11.4
backup dated 2024-02-13 is when it upgraded away from 10.11.4. Both image policies
now allow only 10.x (`^10.5.12`), in `kubernetes/apps/photoprism/image.yaml` and
`kubernetes/charts/photoprism/templates/image.yaml`.

**The data reads correctly today.** Both are serving, and on 2026-10-12 each one's
logical dump, `mariadb-dump --single-transaction --databases`, loaded cleanly into a
fresh `mariadb:10.11.19` with every table's row count matching live, bar rows written
since. So a rebuild from a dump is proven to work, on 10.11 at least.

**Their claims are not backed up.** `photoprism-database-pvc` and
`salamander-database-pvc` carry `k8up.io/backup: "false"`, the dumps standing in for
them. A data directory set aside on the claim during this work is therefore not
backed up either, which is fine for a rollback copy kept a few weeks.

## Decision needed -- user's call

**Which MariaDB to settle on.** Staying on 10.11 keeps the image policies as they
are. Moving to a newer long-term release (11.4 or 11.8) means widening both policies'
ranges and the tag in `kubernetes/apps/salamander.yaml`, and loading the dump into
that version instead. Check each release's end of life before deciding; not verified
here.

## Approach

Per application, one at a time, photoprism first, being the smaller:

1. Take a fresh dump just before, either a one-off dumps-only K8up Backup or the
   same `mariadb-dump` by hand, and check its completion marker.
2. Scale the application and its database to zero.
3. Move the old data directory aside on the claim, so the rollback is a rename.
4. Start the database on an empty directory, at the chosen version, with the same
   `MARIADB_*` environment, so it creates the database and its user.
5. Load the dump. `--databases` dumps create their own database.
6. Compare every table's row count with the dump's source.
7. Start the application, and check it works: library browsing, a search, login.
8. Remove the set-aside directory once it has run cleanly for a while.

salamander's database is a Helm chart template: a change to
`kubernetes/charts/photoprism/templates/` needs a `Chart.yaml` version bump, or Flux
never packages it.

## Verification, when it is done

- No `change buffer` error at start, on either server.
- `mariadb_upgrade_info` names the running version.
- Row counts match the dump, table for table.
- The next scheduled dump of each lands, and the coverage check reads it back whole.

## Prompt to open with

> Read `todos/mariadb-datadir-rebuild.md`. The photoprism and salamander MariaDB
> servers run 10.11.19 on data directories last upgraded by 11.0.2 and 12.2.2, and
> log a change-buffer error at every start. First confirm that is still true, and ask
> me which MariaDB version to settle on. Then rebuild each data directory from a
> fresh dump, photoprism first, keeping the old directory as a rollback, and verify
> row counts against the dump before starting the application.
