# beets-flask bugs to report upstream

Found while deploying and using [beets-flask](https://github.com/pSpitzner/beets-flask)
**v2.0.0-rc5** on 2026-08-13. All reproduced against that image; none reported yet.

Ordered by how much they cost. Each is written to be liftable into an issue —
observed behaviour, evidence, and where known, the cause.

Environment for all of them: `pspitzner/beets-flask:v2.0.0-rc5`, Kubernetes,
config and library on separate volumes, beets 2.11.0 in the image.

---

## 1. A session that fails mid-import cannot be recovered through the UI

**Severity: high.** Leaves an album permanently unimportable without direct
database access.

When an import fails part-way — a timeout, a worker restart — the session is
stranded, and every route out of it fails:

| Action | Result |
|---|---|
| `DELETE /api_v1/session/id/<id>` | HTTP 500 |
| Retag (re-enqueue a preview) | Accepted, then the worker dies on the job |
| Undo | `UserError: Cannot undo if never imported!` |
| Redo | `UserError: Cannot redo imports. Try undo and/or retag!` |

The last two are the user-visible symptom: undo and redo each tell you to do the
other.

**Cause.** The schema has a cycle. `task.chosen_candidate_id` references
`candidate.id`, and `candidate.task_id` references `task.id`. SQLAlchemy cannot
order deletes across it:

```
sqlalchemy.exc.CircularDependencyError: Circular dependency detected.
  (DeleteState(<TaskStateInDb at 0x…>), DeleteState(<CandidateStateInDb at 0x…>))
  File "/venv/lib/python3.12/site-packages/sqlalchemy/orm/unitofwork.py", line 456, in execute
```

Anything that clears a session hits it, which is why all four routes fail.

**Suggested fix.** SQLAlchemy's `relationship(..., post_update=True)` on one side
of the cycle, or nulling `task.chosen_candidate_id` before the cascade runs.

**Workaround.** Update the session row rather than deleting it — set
`session.progress` and `task.progress` to a state matching reality and
`session.exc` to NULL. Updates avoid the cycle. Procedure in
[`docs/beets.md`](../docs/beets.md).

**Related:** a failed undo can create a *second* session for the same
`folder_hash`, which is how the contradictory messages arise — they come from
different rows. 2 of 516 folders ended up in this state here.

---

## 2. The rq job timeout is hardcoded, making long albums unimportable

**Severity: high** for spoken-word libraries.

`backend/beets_flask/redis.py`:

```python
preview_queue = Queue("preview", connection=redis_conn, default_timeout=600)
import_queue  = Queue("import",  connection=redis_conn, default_timeout=600)
```

There is no configuration for it. Any import whose stages exceed ten minutes
fails with `JobTimeoutException` and leaves a stranded session (bug 1), so the
two compound.

Hit here with ReplayGain analysis, which shells out to ffmpeg at roughly 100×
realtime — so the practical ceiling is about a fifteen-hour audiobook:

| Album | Length | Result |
|---|---|---|
| Dawnshard | 7.1 h | imported |
| Teresa: Everybody Loves Large Chests (Vol.5) | 14.9 h | imported |
| Arcanum Unbounded | 22.5 h | `JobTimeoutException` mid-import |

**Suggested fix.** Expose the timeout as a config option. A related consideration
is that a single long job also blocks the one import worker, so users with large
files may want both a longer timeout and more workers.

**Workaround.** Move the slow stage out of the import path — here, disabling
`replaygain.auto` and running `beet replaygain -a` on a schedule instead.

---

## 3. The documented `requirements.txt` plugin mechanism installs into the wrong environment

**Severity: medium.** Fails silently — it reports success and changes nothing.

`docker/entrypoints/entrypoint_user_scripts.sh`:

```sh
if [ -f /config/requirements.txt ]; then
    echo "Installing pip requirements from /config/requirements.txt"
    pip install -r /config/requirements.txt
fi
```

Bare `pip` resolves to the system interpreter, not the application's venv:

```
$ command -v pip     ->  /usr/local/bin/pip
$ pip -V             ->  pip 25.0.1 from /usr/local/lib/python3.12/site-packages/pip (python 3.12)
$ ls /venv/bin/pip   ->  no such file
```

The app runs from `/venv`, so packages land where it will never look. Appears to
be fallout from the migration to `uv` — `/venv` has no `pip` at all.

**Suggested fix.** `uv pip install --python /venv/bin/python -r …`, or invoke
`/venv/bin/python -m pip`. `docs/plugins.md` documents this path, so it is worth
fixing rather than removing.

**Workaround.** Use `startup.sh` with an explicit `VIRTUAL_ENV=/venv uv pip install`.

---

## 4. beets-flask cannot run on a CPU without AVX2

**Severity: medium**, and invisible until it happens — the container stays up.

v2 depends on `polars>=1.36.1`, which resolves to `polars-runtime-32`, compiled
for x86-64-v3. On a CPU without those instructions every uvicorn worker dies at
import:

```
RuntimeWarning: Missing required CPU features.
  avx, avx2, fma, bmi1, bmi2, lzcnt, pclmulqdq, movbe
Illegal instruction        (SIGILL, exit 132)
```

The failure is quiet in a bad way: the container keeps running, the log prints
`Server running on http://0.0.0.0:5001`, redis and the rq workers start normally,
and the workers respawn about eleven times a second while nothing ever binds the
port. There is no traceback in the container log — it only appears if you run the
app factory by hand.

This affects any virtualised guest presenting a generic CPU model (QEMU/Proxmox
`kvm64` here) and older hardware generally.

**Suggested fix.** Depend on `polars[rtcompat]`, or fall back to it at runtime,
or document the requirement. polars ships `polars-runtime-compat` for exactly
this case and installing it is sufficient — no code changes needed.

**Workaround.** A `startup.sh` that probes `import polars` and installs
`polars-runtime-compat` at the matching version when it fails.

---

## 5. `GET /api_v1/session/id/<id>` returns 500

**Severity: low.** Reproduced on a session that had failed mid-import; likely the
same cycle as bug 1 surfacing during serialization. Worth checking whether it
also affects healthy sessions before filing — that was not tested here.

---

## 6. The watchdog's initial scan is wiped by `FLUSHALL`

**Severity: low.** Inferred rather than proven, so verify before filing.

`docker/entrypoints/entrypoint.sh` backgrounds the watchdog and *then* flushes
redis:

```sh
python ./launch_watchdog_worker.py &
redis-cli FLUSHALL >/dev/null 2>&1
uvicorn ...
```

On first start the watchdog logged 553 `Watchdog: Enqueuing …` lines, after which
both queues were empty and no rq registries existed — consistent with the flush
landing after the enqueues. Reordering the flush before the watchdog launch would
close the race.

---

## 7. `docs/plugins.md` describes the wrong base image

**Severity: trivial**, but actively misleading. The page tells users to install
plugin build dependencies with `apk` and says "the container is based on alpine".
v2 moved to `python:3.12-slim`, so those instructions fail. The v2.0.0 changelog
records the base image change; the plugin docs were not updated with it.

---

## 8. `pragma foreign_key_check` fails on the schema

**Severity: trivial**, noted so it is not mistaken for corruption.

```
sqlite3.OperationalError: foreign key mismatch - "session" referencing "folder"
```

Present in an untouched backup, so it is a property of the schema — the `session`
→ `folder` foreign key does not resolve to a unique index on the parent.
`pragma integrity_check` returns `ok`. Harmless in practice, but it makes the
standard consistency check unusable.
