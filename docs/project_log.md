# Completed projects

An index of finished work: the slug it used, when it landed, and where its review
happened. Newest first.

This is not a description of what changed — git carries that. What lives here is
what git does not: a registry of spent slugs, so a name is never reused, and the
address of the review discussion.

Deleted specs are recoverable: `git log --diff-filter=D -- todos/<slug>.md`

Entries below predate the review workflow and so have no pull request. They are
backfilled from the commits that retired their specs.

## disc-ripping-reliability

2026-08-25 · retired by `f85c527`

Gated the ripper on udev media properties rather than drive status.

## proxmox-cpu-type

2026-08-18 · retired by `8d5c017`

Gave the VMs the host CPU instead of a 2008-era model.
See [talos.md](talos.md).

## control-plane-memory

2026-08-17 · retired by `52a5b7f`

Resized the control plane and documented how it is sized.
See [talos.md](talos.md).

## beets-flask-upstream-bugs

2026-08-15 · retired by `3452836`

Not completed here — the bugs moved to the `beets-flask` fork, where they are
tracked as contribution specs. The slug is spent regardless.

## promtail-to-alloy-prompt

2026-08-10 · retired by `372c0ea`

Replaced promtail and Loki with Alloy shipping OTLP to VictoriaLogs.
See [logging.md](logging.md).
