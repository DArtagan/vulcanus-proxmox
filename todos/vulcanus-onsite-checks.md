# Physical checks on vulcanus, for the next visit in person

Everything here needs hands on the machine. Will expects to be at the house in
about a month from 2026-10-06. Until then this spec collects what to check, in
what order, and how to tell whether each change made a difference.

The main target is the optical drive, which keeps losing its SATA link
(`todos/disc-ripping.md`, progress log from 2026-09-22 on). A data disk on the
same controller logged link-layer errors of its own between June and September,
and since it is one cable over, it is in scope too.

## What is known, verified 2026-10-06

**The drive hang reproduces without a disc, MakeMKV or the VM.** One 4-byte
`READ BUFFER 0x77 @0x121000`, sent from the host with `sg_raw`, got no response,
timed out at 30 s and left the drive deaf to link resets until a cold power
cycle. It is the same command, and the same signature, as every MakeMKV-context
hang that has been traced. One run so far, with a confound. See the 2026-10-06
entries in `todos/disc-ripping.md`.

**The drive's failures sometimes show corruption on the wire.** Of 33 `ata4`
exceptions since April, 30 are command timeouts on a clean link (`SErr 0x0`).
The two where the link itself dropped, 2026-04-19 and 2026-09-22, logged
`10B8B`: symbols the host could not decode. That points at the physical layer
(cable, connectors, power, or the drive's own SATA interface), but the 30
timeouts don't say which.

**`sda` on the same controller had link-layer failures** on 17 days between
2026-06-07 and 2026-09-06, none since: `interface fatal error`,
`SError: { UnrecovData Handshk }` on queued writes, then a link reset. `sda` is a
10 TB HGST in `rpool`. ZFS absorbed them. The pool is healthy, and the
2026-09-13 scrub repaired 0 B.

**Lifetime CRC error counts** (`UDMA_CRC_Error_Count`, undated): `sda` 31, `sdd`
49, `sde` 1, `sdf` 1, all others 0. `sdd` is on the *other* controller, so the
counts don't single out the drive's controller.

### SATA topology

Board: MSI X99A GAMING PRO CARBON (MS-7A20). From `/sys/class/ata_port` and
`/dev/disk/by-path`, 2026-10-06:

| kernel port | controller | device | notes |
|---|---|---|---|
| ata1 | sSATA `00:11.4` | `sda` HGST HUH721010AL (rpool) | link errors Jun–Sep, CRC 31 |
| ata2 | sSATA `00:11.4` | `sdb` ST4000VN008 | |
| ata3 | sSATA `00:11.4` | `sdc` HGST HUH721010AL | |
| ata4 | sSATA `00:11.4` | `sr0` Pioneer BDR-212U | the optical drive |
| ata5 | SATA `00:1f.2` | `sdd` ST4000VN008 | CRC 49 |
| ata6 | SATA `00:1f.2` | `sde` ST4000VN008 | |
| ata7 | SATA `00:1f.2` | `sdf` HGST HUH721010AL | |
| ata8 | SATA `00:1f.2` | `sdg` ST4000VN008 | |
| ata9 | SATA `00:1f.2` | — | no device: the free port |
| ata10 | SATA `00:1f.2` | `sdh` HGST HUH721010AL | |

`docs/automatic-ripping-machine.md` says the drive is on the board's "SATA Port
10". How kernel ports map to the board's printed labels has **not** been checked.
Confirm it on site before moving anything. Disks are named in `rpool` by
`/dev/disk/by-id` (model and serial), so moving a disk between ports doesn't
confuse ZFS. The drive's `/dev/optical-drive-sg` symlink matches its model
string, so it follows the drive to any port.

## The test to run after each change

The disc-free reproducer is the measuring stick. A change has made a difference
only if the reproducer's hang rate drops against a baseline taken beforehand.
One clean attempt after a change proves little if the baseline isn't close to
100%.

```bash
# on vulcanus, with ARM idle and nothing in the drive
sg_raw -r 4 -t 30 /dev/optical-drive-sg 3c 02 77 12 10 00 00 00 04 00
```

- **Hang:** no output for ~30 s, then `ata4` (or the drive's new port) logs an
  exception with `pio 16388 in`. The drive is usually disabled, and only a cold
  power cycle brings it back: `poweroff`, at least 30 s off, then power on.
- **Answer:** returns at once, either with 4 bytes or with sense data. An
  `ILLEGAL REQUEST` is also a non-hang.

What the baseline needs, and should be measured **before** the visit, in sessions
from `todos/disc-ripping.md`:

- Whether the command hangs straight after a cold power cycle, before MakeMKV has
  touched the drive. If it doesn't, each attempt must follow a MakeMKV run, and
  the protocol below changes.
- The rate over several attempts, run the same way each time.

If the baseline can't be established remotely, take it on site first, before
changing anything. That costs a few extra power cycles.

Trace each attempt so a hang can be compared with the earlier ones. The method,
with the libata per-command events added, is in the 2026-10-04 and 2026-10-06
entries of `todos/disc-ripping.md`.

## Checks, in order

Cheapest and least disruptive first, then the ones that move things. After each,
run the reproducer as above, and record the result in the table at the bottom.

**Before opening the case:** check every guest has `onboot=1`, no vzdump or
`zfs send` is running, and `zpool status -x` is clean (the same checks as the
power-cycle procedure). Then `poweroff`.

1. **Record what is there before touching it.** Photograph the drive's SATA data
   cable and power lead end to end. Note: which board port the drive uses, and
   which port `sda` uses (check against the table above); cable lengths, whether
   they latch, and how they are routed; where the drive gets its power, and
   whether through Molex adapters or splitters; whether it shares a lead with
   `sda` or other disks. Also note what the drive's enclosure is: a bay in the
   case, or a separate box with its own power or a SATA/eSATA extension. Nothing
   here records what the enclosure is.
2. **Reseat the drive's data and power connectors at both ends.** Reproducer.
3. **Replace the drive's SATA data cable** with a new, short, latching cable.
   Reproducer.
4. **Give the drive its own PSU power lead** rather than a splitter or adapter,
   if step 1 found one. Reproducer.
5. **Move the drive to the free port on the other controller** (`ata9`,
   `00:1f.2`, once its board label is confirmed). This separates the port and
   controller from the cable and drive. After it, the drive will no longer be
   `ata4`, so these need updating: `ansible/proxmoxer.yaml` (the monitor greps
   `ata4`; the recovery script hard-codes `host3`), the
   `kubernetes/infrastructure/prometheus-rules.yaml` description, and
   `docs/automatic-ripping-machine.md`. Check `qm showcmd 911` still opens
   `/dev/optical-drive-sg`, and that the symlink resolves. Reproducer.
6. **`sda`: reseat, then replace, its data cable**, independently of the drive's
   results, because of its June–September errors. Do it powered off. Afterwards:
   `zpool status` shows all disks `ONLINE`, then start a scrub and watch
   `journalctl -k | grep ata1` for the following weeks. Don't move `sda` to
   another port in the same visit as the drive move, or a later error can't be
   attributed to either change.

Stop changing things as soon as the drive's reproducer stops hanging. Further
changes would only blur which one did it. Note it, then confirm it with real rips
of the discs that hung most: *Around the World in 80 Days* disc 2 and *Sherlock
Holmes in the 22nd Century*.

If none of steps 2–5 changes the hang rate, the drive itself is the remaining
suspect. That leads to the avenues below that Will has already declined, so
bring it back to him rather than choosing.

## Decisions already made

- **Declined for the drive** (Will, in a session before 2026-10-05; first
  written down 2026-10-06): turning off MakeMKV's LibreDrive,
  reducing `makemkvcon` runs per job, making VM 911 boot without the drive, and
  drive firmware changes. "No, I don't think any of these avenues are ones we'd
  like to pursue." Ask before proposing any of them again.
- **No tray movement without asking.** The drive's enclosure is closed when
  nobody is ripping.
- **Priorities:** data integrity first, then the ability for someone at the house
  to rip without help (Will, 2026-08-24).
- **The kernel A/B test is deprioritised** (2026-10-06). The only clean kernel,
  7.0.0-3, was used for just 17 runs, and 17 clean runs is about a 38% chance at
  the rate seen on the next kernel. See `todos/disc-ripping.md`.

## Results

Fill in on site, one row per reproducer attempt.

| step | change | attempt | hang? | notes |
|---|---|---|---|---|
| baseline | — | | | |

## Opening prompt

> I'm at the house in person and can open vulcanus. Read
> `todos/vulcanus-onsite-checks.md`, then the 2026-10-06 entries in
> `todos/disc-ripping.md`. First confirm the reproducer baseline exists. If it
> doesn't, take it before anything changes. Then walk me through the checks in
> order, one at a time. Run the reproducer after each and fill in the results
> table. Stop changing things the moment the drive's hang rate drops, and help
> me update the `ata4` references if the drive moves port. Don't propose any of
> the declined avenues without asking.
