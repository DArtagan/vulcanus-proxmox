# Make a ConfigMap change actually reach the running process

## Opening prompt

> Changing a ConfigMap in git does not restart the workload that reads it, so
> the cluster can run configuration that no longer matches the repository, with
> nothing to indicate it. This has already caused two silent misbehaviours in
> the beets stack. Read `todos/config-change-rollouts.md` and decide between the
> two approaches it describes — the user has expressed interest in Reloader —
> then implement it.

## The problem

Flux applies an updated ConfigMap immediately, but a Deployment whose pod
template did not change is not restarted. The pod keeps running with whatever it
read at startup. Nothing fails, nothing warns, and `flux get kustomizations`
reports everything reconciled and healthy — because it is. The desired state
*was* applied; the process simply never re-read it.

Two shapes of this, both live in this repo:

- **Directly mounted ConfigMaps** (7 Deployments). The kubelet refreshes the
  files within its sync period, so the files on disk are current, but a process
  that reads its config only at startup carries on with the old values.
- **beets-flask**, which is worse. Its `/config` is a PVC seeded by an
  initContainer from the ConfigMap, so even the *file* stays stale until the pod
  is recreated. See `docs/beets.md`.

## Evidence — this is not hypothetical (2026-08-13)

It bit twice in a single session, both silently:

1. The commit fixing beets' `paths` ordering touched only `config-map.yaml`. Flux
   applied it, no rollout happened, and the pod kept the old file with `default`
   first — which matches every item, making the genre rules unreachable. Five
   audiobooks were imported to `music/` instead of `audiobooks/` during that
   window. Nothing reported an error; the imports "succeeded".
2. The commit disabling `replaygain.auto` had the same shape. Caught before the
   next import only because the drift was checked for deliberately:

   ```
   seeded config replaygain : {'backend': 'ffmpeg'}
   cluster ConfigMap        : {'backend': 'ffmpeg', 'auto': False}
   ```

Both were fixed with `kubectl rollout restart`, which is the interim workaround.

## Scope

Eight Deployments consume a ConfigMap and would benefit:

```
apps/automatic-ripping-machine   apps/beets        apps/borgmatic
apps/botamusique                 apps/headscale    apps/homepage
apps/mumble                      apps/youtube-dl
```

The three CronJobs that consume ConfigMaps (`beets`, `beets-replaygain`,
`pinepods`) are unaffected — each run gets a fresh pod.

## Option A — stakater/Reloader

A controller that watches ConfigMaps and Secrets and restarts workloads carrying
an annotation:

```yaml
metadata:
  annotations:
    reloader.stakater.com/auto: "true"
```

- Fixes all eight with one annotation each and no restructuring.
- Works for **SOPS-encrypted Secrets too**, which Option B cannot: a generator
  needs plaintext at build time, so encrypted secrets must keep stable names.
  This repo has several.
- Cost: another controller to run, watch and keep updated — and per
  `docs/README.md`, chart versions are automated only where the chart ships as
  an OCI artifact, so check which case Reloader falls into before assuming the
  version will track itself.

**The user's stated leaning:** *"stakater/reloader seems pretty cool."*

## Option B — Kustomize `configMapGenerator`

Kustomize appends a content hash to the ConfigMap name and rewrites every
reference to it, so a changed config produces a changed pod template and Flux
rolls the workload on its own. Verified against this repo's toolchain:

```
kind: ConfigMap
  name: beets-config-map-677m22f998
kind: Deployment
          name: beets-config-map-677m22f998    ← rewritten automatically

after changing one byte:
  name: beets-config-map-mt775d9452            ← both move together
```

- No extra controller; the reference *is* the checksum, so they cannot drift.
- Config data moves out of embedded YAML strings into real files. For beets that
  is a genuine gain: `audiobook_genre.py` becomes an actual Python file that can
  be linted rather than a string inside a ConfigMap.
- Requires restructuring each app's ConfigMap, and does nothing for Secrets.

Things that bite:

- Old ConfigMaps are orphaned on every change. `prune: true` on the `apps`
  Kustomization cleans them up here, but this surprises people.
- Every reference must be inside the same kustomization; the transformer does
  not reach outside it. Both beets CronJobs are in the same directory, so they
  are fine, but a cross-directory reference would break the moment a hash
  changed.
- Every change rolls the pod, including comment-only edits. For beets-flask that
  costs about forty seconds and a 54 MB polars re-download from `startup.sh`.
- Do not copy `disableNameSuffixHash: true` from examples — it disables the
  whole mechanism.

## Notes for whoever picks this up

- The Helm `checksum/config` annotation everyone reaches for first does not
  translate. It is template-time hashing; Kustomize has no templating, so the
  literal equivalent means pasting a SHA by hand — reintroducing the manual step
  the work is meant to remove.
- The usual simplest answer — mount the ConfigMap and let the kubelet sync it,
  no restart at all — does not help either shape of the problem here. The
  process still has to re-read the file, and beets-flask needs a *writable*
  `/config`, which is why it seeds a PVC in the first place.
- The two options are not exclusive. Reloader for Secrets and for apps whose
  ConfigMaps are awkward to restructure; `configMapGenerator` where the config
  wants to be real files anyway. Deciding one for the whole repo is simpler to
  reason about, but the split is defensible.
