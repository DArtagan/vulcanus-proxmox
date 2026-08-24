# Put the vzdump backup job under Terraform

Blocked on a `bpg/proxmox` release. Verified 2026-08-24. Nothing to do until
that release exists; re-check the version list and pick this up when it does.

## What this is about

The nightly Proxmox backup job lives only in `/etc/pve/jobs.cfg` and is invisible
to this repo. Two settings that decide how hard it hits `rpool` were applied by
hand with `pvesh` on 2026-08-23:

```
--exclude       100,101,106,107   (was 100,107)
--performance   max-workers=2     (default 16)
```

Why each, in full, is in [`docs/talos.md`](../docs/talos.md) under "How the
backup job is tuned". The whole job as it stands:

```
schedule 4:00 · all 1 · mode snapshot · storage pbs · compress zstd · quiet 1
exclude 100,101,106,107 · performance max-workers=2 · prune-backups keep-last=31
mailnotification failure · mailto <address> · node vulcanus · enabled 1
```

This job is the only thing protecting the OpenEBS PVC data — see
[`backups.md`](backups.md) — so it is worth being reviewable and restorable.

## The blocker

`telmate/proxmox` 3.0.2-rc07 has no backup-job resource at all. Its entire
schema, from `tofu providers schema -json`, is seven resources:
`proxmox_cloud_init_disk`, `proxmox_lxc`, `proxmox_lxc_disk`, `proxmox_lxc_guest`,
`proxmox_pool`, `proxmox_storage_iso`, `proxmox_vm_qemu`.

`bpg/proxmox` has `proxmox_backup_job`, added in 0.99.0 (2026-03-21). **But no
released version implements `exclude`.** Confirmed two ways against 0.111.1, the
newest of the 187 versions in the registry:

```
guest-selection attributes: ['all', 'exclude_path', 'pool', 'vmid']
exclude present? False
```

and the docs at the `v0.111.1` tag list only `all`, `vmid`, `pool` and
`exclude_path` — the last being filesystem globs, not guests. `exclude` **is**
merged on `main`; it was added after 0.111.1 was cut. bpg released every 5–7 days
through June 2026 and has released nothing since 0.111.1 on 2026-07-03.

**The wrong turn worth inheriting:** the plan for this was written from
`raw.githubusercontent.com/.../main/docs/resources/backup_job.md`, which
documents `exclude`, and only failed at `tofu plan` with `An argument named
"exclude" is not expected here`. Read provider docs at the tag you intend to
pin, not at `main`.

Pinning the merged commit is not an option: providers come from a registry, and
`dev_overrides` is a development facility, not something to run applies through.

## What was already proven, on 2026-08-24

Worth not redoing. The rest of the approach works:

- `tofu init` installs `bpg/proxmox` alongside `telmate/proxmox` cleanly.
- Both claim the `proxmox_` resource prefix, so `proxmox_backup_job` needs an
  explicit `provider = bpg` meta-argument. With it, the type resolves correctly
  and the telmate VM resources show no diff.
- No `ssh` block is needed — bpg wants one only for snippet upload and disk
  import.
- Credentials already exist and already suffice. `PVE::API2::Backup` checks
  `Sys.Modify` on `/` for create, update and delete; the `terraform@pve` user
  holds the custom `Terraform` role on `/` with propagate, and that role includes
  `Sys.Modify`. `TF_VAR_proxmox_api_token_id` and `TF_VAR_proxmox_api_token_secret`
  are already in `.env`.
- `var.proxmox_api_url` carries the `/api2/json` suffix telmate wants; bpg
  addresses the API root, so it needs `trimsuffix(var.proxmox_api_url, "/api2/json")`.
- bpg models no `quiet`. Adopting the job drops `quiet 1`, which is read at
  `PVE::API2::VZDump.pm:158-159` and only redirects the worker's stdout and
  stderr to `/dev/null`. Task logs are written by the task logger regardless, so
  the effect is at most extra lines in `/var/log/pve/tasks`.
- PVE accepts a chosen job id on create (`--id`), so the generated hash
  `9af0fe23b91fd64972f0d7b4a414b8c912dbf200:1` does not have to be inherited.

## Decisions already made — user's call, 2026-08-24

- **Recreate rather than import.** Delete the hash-id job and let Terraform
  create `nightly-pbs`, so the id in git is readable and matches the host. Do it
  during the day: the window costs nothing, but a half-finished apply leaves no
  backup job overnight.
- **`vmid` is not an acceptable substitute for `all` + `exclude`.** It inverts
  the safety property — a guest created later would be silently unbacked-up,
  with no Prometheus visibility into vzdump to catch it. Offered and declined.
- **An Ansible playbook was offered and declined** in favour of waiting for a
  typed resource with real drift detection.
- **The VMs stay on `telmate`.** Nothing there is broken, and the definitions
  carry detail that a rewrite could quietly lose: `cpu { type = "host" }` and its
  `SIGILL` rationale, the optical-drive `args` passthrough that `docs/talos.md`
  records the Proxmox API cannot express, `discard`, `startup_shutdown`. If that
  is ever revisited, the method is `tofu import` per VM plus a before/after diff
  of the rendered `qm config`, not of the HCL.

For the record on provider choice, since the question will recur: telmate ships
7 resources against bpg's ~111 and is on its ninth 3.0.2 release candidate with
no stable release, while bpg is 0.x and so permits breaking changes between
minors. Neither is abandoned — telmate's rc09 (2026-08-14) is *more* recent than
bpg's 0.111.1 (2026-07-03).

## How to check whether it is unblocked

```bash
curl -s https://registry.opentofu.org/v1/providers/bpg/proxmox/versions \
  | python3 -c "import json,sys; print([v['version'] for v in json.load(sys.stdin)['versions']][-5:])"
```

If anything newer than 0.111.1 exists, confirm the attribute is really there
before writing HCL against it — the schema from the installed binary is the only
authority:

```bash
cd terraform && tofu providers schema -json \
  | python3 -c "import json,sys; b=[v for k,v in json.load(sys.stdin)['provider_schemas'].items() if 'bpg' in k][0]; print('exclude' in b['resource_schemas']['proxmox_backup_job']['block']['attributes'])"
```

## Verification, when it is done

1. `tofu plan` — one resource to add, and **no diff on the telmate VMs**.
2. Delete the old job, `tofu apply`.
3. `pvesh get /cluster/backup --output-format json` — exactly one entry, id
   `nightly-pbs`, `exclude: 100,101,106,107`, `performance: {max-workers: 2}`,
   `next-run` at 04:00 local tomorrow.
4. `tofu plan` again — **must be empty.** PVE normalises some fields on write
   (`schedule` may come back as `4:00` rather than `04:00`); anything it rewrites
   would otherwise be a permanent diff on every future plan.
5. Next morning, confirm the job actually ran:
   `ssh root@vulcanus.forge.local 'grep -h vzdump /var/log/pve/tasks/index | tail -1'`

Unrelated but in the way: `tofu plan` already reports drift on
`local_sensitive_file.kubeconfig` (replace) and `proxmox_lxc.fileserver`
(in-place). Both predate this work. Expect them in any plan output here.

## Prompt to open with

> Read `todos/vzdump-job-in-terraform.md`. The nightly Proxmox backup job lives
> only in `/etc/pve/jobs.cfg`, and two settings applied by hand on 2026-08-23 —
> `--exclude 100,101,106,107` and `--performance max-workers=2` — exist nowhere
> in git. Bringing it under Terraform needs `bpg/proxmox`'s `proxmox_backup_job`,
> which was blocked because no released version implemented `exclude`; it is
> merged on `main` but 0.111.1 (2026-07-03) predates it. Check whether a newer
> release now carries it, confirming against the installed binary's schema rather
> than the docs on `main`. If it does, implement per the spec; the approach was
> already proven to work apart from that one attribute. If it still does not,
> stop and say so rather than falling back to `vmid`, which was explicitly
> declined.
