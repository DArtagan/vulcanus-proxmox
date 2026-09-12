# Pod security is restated per-workload because nothing enforces it per-namespace

## Opening prompt

> Deployments that care about security restate `allowPrivilegeEscalation:
> false`, `capabilities.drop: [ALL]` and `seccompProfile` individually, which is
> the wrong layer — it is policy, and policy belongs to the namespace. Read
> `todos/pod-security-namespace-level.md`. The blocker is that most existing
> workloads would fail `restricted`, so this is an audit-then-enforce job rather
> than a label change.

Verified **2026-09-11**.

## What exists

Namespace labels, read from the cluster:

| Namespace | Pod Security Admission |
|---|---|
| `apps` | **none at all** |
| `infrastructure` | `enforce: privileged`, `warn: restricted`, `audit: restricted` |
| `automatic-ripping-machine` | `enforce: privileged` |

With no labels, `apps` falls back to Talos' cluster-level `AdmissionConfiguration`
— `enforce: baseline`, `warn: restricted` — which is why `kubectl apply` prints
a `would violate PodSecurity "restricted:latest"` warning there and then admits
the pod anyway. Nothing is enforced above baseline anywhere.

There is no Kyverno or Gatekeeper; `kubectl get crd` shows no policy engine.

## The defect

Because nothing enforces it, a workload that wants the restricted profile has to
declare it itself. That is policy expressed per-workload: every new Deployment
is a chance to forget, and the ones that do declare it are indistinguishable
from the ones nobody considered. Four files currently set a `securityContext`
(`beets`, `pinepods`, `tinyproxy`, `automatic-ripping-machine`) and most set it
for `runAsUser`, not for the restricted profile at all.

## Why it is not a one-line fix

Labelling `apps` with `enforce: restricted` would reject most of what runs
there. `automatic-ripping-machine` genuinely needs `SYS_ADMIN` for optical
device access and is already `enforce: privileged` for that reason. The work is
to find out what each workload actually needs, not to set a label.

## Plan

1. Turn on reporting without enforcement first: label `apps` with
   `warn: restricted` and `audit: restricted`, matching `infrastructure`.
   Nothing is rejected; violations become visible.
2. Read the audit annotations and list what each workload violates. Expect two
   groups — those needing one line of `securityContext`, and those with a real
   requirement.
3. Fix the first group, and give the second its own namespace label or an
   explicit exemption with the reason recorded.
4. Move `apps` to `enforce: restricted`, then delete the per-workload
   restatements that the namespace now guarantees.
5. Reconsider `infrastructure`, which is `enforce: privileged` while auditing
   `restricted` — that gap is presumably deliberate for the storage and
   networking components, but it is not written down anywhere.

## Note

Raised in review of `dnsomatic-replacement` ([PR #5](https://github.com/DArtagan/vulcanus-proxmox/pull/5)),
where `cloudflare-ddns` had exactly this boilerplate. It was removed there
rather than added to, so the count of workloads restating policy did not grow
while this is outstanding. `runAsNonRoot` stayed, because the updater checks for
root and warns — that is a property of the workload, not blanket policy.
