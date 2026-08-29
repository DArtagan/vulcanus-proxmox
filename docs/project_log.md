# Completed projects

An index of finished work: the slug it used, when it landed, the commit that
retired its spec, and where its review happened. Newest first.

This is not a description of what changed — git carries that. What lives here is
what git does not: a registry of spent slugs, so a name is never reused, and the
address of the review discussion.

Deleted specs are recoverable: `git log --diff-filter=D -- todos/<slug>.md`

Entries without a pull request predate the review workflow. Sub-bullets carry
whatever does not fit the one-line shape.

* disc-ripping-reliability (2026-08-25, `f85c527`): Gated the ripper on udev media properties rather than drive status.
* proxmox-cpu-type (2026-08-18, `8d5c017`): Gave the VMs the host CPU instead of a 2008-era model. See [talos.md](talos.md).
* control-plane-memory (2026-08-17, `52a5b7f`): Resized the control plane and documented how it is sized. See [talos.md](talos.md).
* beets-flask-upstream-bugs (2026-08-15, `3452836`): Nine bugs found running beets-flask v2.0.0-rc5, collected for upstream.
  * Relocated rather than completed here — they are tracked as contribution specs in the `beets-flask` fork. The slug is spent regardless.
* promtail-to-alloy-prompt (2026-08-10, `372c0ea`): Replaced promtail and Loki with Alloy shipping OTLP to VictoriaLogs. See [logging.md](logging.md).
