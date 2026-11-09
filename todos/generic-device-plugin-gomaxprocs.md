# Test whether generic-device-plugin still needs its GOMAXPROCS pin

## Opening prompt

> Read `todos/generic-device-plugin-gomaxprocs.md`. The plugin runs with
> `GOMAXPROCS=2` and a 64Mi memory limit. Remove the pin, change nothing else,
> and read the outcome off the measures below over two weeks.

## Where things stand

As of 2026-11-05, see "Keeping the plugin healthy" in
`docs/automatic-ripping-machine.md`. The plugin's degradation was its 20Mi
memory limit, which was smaller than its own code plus heap
(`generic-device-plugin-memory`, closed 2026-11-05). The GOMAXPROCS pin had been
credited with ending it, until it degraded again with the pin in place. What the
pin demonstrably does is lower resident memory: unpinned on the 8-core worker
RSS was 10–14.6Mi, pinned 7.7Mi. Under 20Mi that decided whether the code stayed
resident. Every degraded measurement of an unpinned runtime (11–18ms pauses on
the 4- and 8-core nodes, August–September) was taken under the 20Mi limit, so
none of them separates the pin's effect from the limit's.

If the pin works only through memory, removing it at 64Mi changes RSS and
nothing else. If the pause rises, a wider runtime has an effect of its own and
the pin stays.

## What decides it

Under the pin, over 2026-10-16 to 11-05: median GC pause 80–100µs typical,
memory.current 18–23Mi, RSS 8.9–9.6Mi, about one major fault a day per process.

**Expected if the pin is unnecessary:** RSS rises on worker-0 (8 cores) and
worker-1 (4 cores), in line with the 10–14.6Mi measured before. memory.current
rises by about the same, staying well under 75% of 64Mi, which is where
`DevicePluginMemoryHigh` fires. Major faults stay near zero and the median pause
stays near 90µs. The control plane has 2 cores, so it is unchanged and serves
as the control.

**The pin stays if** the median pause on worker-0 or worker-1 rises clearly
above the control plane's with memory.current well under the limit and no major
faults.

Worker-1's transcodes count. ARM's jobs run off CPU 0, and the first Blu-ray
encoded that way, on 2026-11-09, left the plugin's median pause at 95µs at
most. If the pause rises only while a transcode runs, check the job's CPU
affinity ("Stage 2: Transcode" in `docs/automatic-ripping-machine.md`) before
reading it as evidence about the pin.

## When it closes

If the pin is unnecessary, delete the `GOMAXPROCS` env and its comment in
`kubernetes/infrastructure/devices.yaml`, the parallelism paragraph in
`docs/automatic-ripping-machine.md`, and the GOMAXPROCS check in
`DevicePluginDegraded`'s description. If it is still needed, rewrite that
paragraph around the measured effect. Either way, if the upstream issue is
still unfiled, correct its Evidence 4 in `todos/generic-device-plugin-upstream.md`.
