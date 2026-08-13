# Give the VMs a CPU that isn't from 2008

## Opening prompt

> The Proxmox VMs are configured with `cpu type = "kvm64"`, which masks every
> instruction set above SSE4.2 — no AVX, AVX2, FMA, BMI1/2, LZCNT, PCLMULQDQ or
> MOVBE. This already blocked one deployment outright and silently costs
> performance everywhere else. Read `todos/proxmox-cpu-type.md`, confirm the
> physical host's capabilities, and switch the VMs to a CPU type that exposes
> them.

## What is wrong

`terraform/modules/proxmox_talos_vm/main.tf` and
`terraform/modules/proxmox_backup_server/main.tf` both set:

```hcl
cpu {
  type = "kvm64"
  ...
}
```

`kvm64` is QEMU's maximally-portable model — roughly a Pentium 4 / K8 with
SSE2. Inside the guests this presents as:

```
model name : Common KVM processor
flags      : … sse4_2 …          # and nothing above it
```

Every Talos node in the cluster runs this way, and has since the cluster was
built.

## How it surfaced (2026-08-13)

Deploying beets-flask v2.0.0-rc5 produced a pod that ran, logged
`Server running on http://0.0.0.0:5001`, and never served a request. Its uvicorn
workers were dying on startup and respawning about eleven times a second — pids
advanced 5011 → 5238 in twenty seconds — with no traceback anywhere in the
container logs.

The cause only appeared when the app factory was run by hand:

```
RuntimeWarning: Missing required CPU features.
    avx, avx2, fma, bmi1, bmi2, lzcnt, pclmulqdq, movbe
Illegal instruction          # exit 132, SIGILL
```

beets-flask v2 depends on `polars>=1.36.1`, which ships its compiled core as a
separate wheel — `polars-runtime-32`, built for x86-64-v3. On this CPU it
SIGILLs the instant it is imported. (beets-flask v1.2.1 has no polars
dependency, which is why this had never bitten before.)

Two things about the failure mode are worth carrying forward:

- **Nothing in the manifest pipeline could have caught it.** `kustomize build`,
  a server-side dry-run and every schema check pass. It is a runtime CPU
  feature, invisible until the process executes.
- **The container stayed healthy while the app was dead.** Only the readiness
  probe distinguished the two. Without it the Deployment would have reported
  `Available`.

## The workaround now in place

`kubernetes/apps/beets/config-map.yaml` carries a `beets-flask-startup.sh` key,
seeded to `/config/beets-flask/startup.sh` by the Deployment's initContainer. It
probes whether `import polars` works and, if not, installs
`polars-runtime-compat` at the version matching the installed polars.

It is written to become a no-op the moment the CPU can run the stock runtime, so
this work does not need to remove it to be effective — but it *should* be
removed, along with its seeding line and the explanatory comment, once this
lands. It costs a 54 MB download on every pod start.

Note for anyone tempted by the documented `requirements.txt` mechanism instead:
it does not work in beets-flask v2. `entrypoint_user_scripts.sh` installs those
with bare `pip`, which is `/usr/local/bin/pip` writing to the system
site-packages, not the application's `/venv`. It reports success and changes
nothing. This appears to be fallout from upstream's migration to `uv`.

## Do this first

Confirm the physical host actually has the features. Everything below assumes
it does, and none of it is worth doing if it does not:

```bash
ssh root@vulcanus.forge.local \
  "grep -m1 'model name' /proc/cpuinfo; \
   grep -m1 ^flags /proc/cpuinfo | tr ' ' '\n' | grep -xE 'avx|avx2|fma|bmi1|bmi2|lzcnt|pclmulqdq|movbe' | sort | tr '\n' ' '"
```

This could not be checked from the Claude session that found the problem — SSH
to the host rejected the available key.

## Choosing a type

- **`host`** — passes the physical CPU through unmodified. Best performance, and
  with a single Proxmox host there is no live-migration reason to avoid it. The
  usual objection (a VM pinned to one host's CPU cannot migrate to a different
  one) does not apply here.
- **`x86-64-v3`** — a defined baseline: AVX, AVX2, FMA, BMI1/2, LZCNT, MOVBE.
  Exactly what polars wants, and stable if a second host ever appears.

`host` is the straightforward choice unless a second hypervisor is planned.

## Applying it

A CPU type change is not hot-appliable: each VM needs a full power cycle, not a
reboot. Talos nodes should go one at a time, control plane first, checking the
cluster settles between each — the same discipline as a Talos upgrade
(`docs/talos.md`).

The Proxmox Backup Server VM (`terraform/modules/proxmox_backup_server`) carries
the same setting and can follow separately; it has no cluster-membership
concerns.

## Afterwards

1. Confirm from inside a pod that the flags are present:
   `kubectl run --rm -it cpucheck --image=busybox --restart=Never -- grep -m1 ^flags /proc/cpuinfo`
2. Redeploy beets-flask and confirm the startup script reports
   `polars imports cleanly, nothing to do`.
3. Remove the workaround: the `beets-flask-startup.sh` ConfigMap key, the two
   lines seeding it in `kubernetes/apps/beets/deployment.yaml`, and the
   paragraph about it in `docs/beets.md`.
4. Record in `docs/` that the VMs expose a modern CPU and why it matters —
   future images will assume AVX2 without saying so.
