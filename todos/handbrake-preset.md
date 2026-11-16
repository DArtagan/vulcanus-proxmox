# HandBrake preset: changes worth making

## Opening prompt

> Review ARM's HandBrake preset against the changes this spec proposes, and make
> the ones Will chooses. Read `todos/handbrake-preset.md`, then
> `docs/handbrake-preset.md`, which explains every setting as it stands, and
> *Encoding* in `docs/automatic-ripping-machine.md`. Test each change by
> transcoding part of a raw copy before it is deployed.

## Why

Will built the preset as *"the best I was able to put together at the time"*
and asked for a full review of it (2026-11-16). `docs/handbrake-preset.md` is
that review: every setting, grouped by purpose, with the accepted practice and
where this preset departs. This spec holds what the review suggests changing.
None of it is decided. The measurements are in
[handbrake-preset.evidence.md](handbrake-preset.evidence.md).

Every disc's raw copy is kept beside its film (reelbarrow's design), so any
change can be applied to titles already transcoded.

## Proposed changes, most effect first

**1. Audio: stop it outweighing the picture.** Audio is 57% of *Downton
Abbey*'s transcode and 70% of *Shrek the Third*'s, against 35% and 29% for
video. Four separate faults add up (*Audio* in the doc):

- A stereo source becomes two stereo tracks, the second at 320 kb/s (job 34:
  178 MB and 299 MB of the same sound). HandBrake has no option to drop a
  duplicate. The ways out are to accept it, or to keep one `AudioList` entry
  and give up either the 5.1 or the separate stereo downmix.
- Every secondary track (other languages, commentaries) is 192 kb/s stereo.
  Xiph puts stereo Opus at transparent by 128 kb/s, and a commentary needs far
  less.
- Every language is kept (`AudioLanguageList` `any`). Keeping English and the
  original language would drop most dubs.
- The −3 dB gain, there to keep a downmix from clipping, also quietens stereo
  sources and every secondary track.

A preset can't vary bitrate by track, beyond first and secondary. The
choices for Will:

- the main track's outputs: 5.1 and a stereo downmix as now, or 5.1 only
  (players and Plex downmix), or a lower stereo bitrate;
- secondary tracks: their bitrate, and which languages to keep;
- the gain: 0 dB with `AudioNormalizeMixLevel` on for downmixes, or −3 dB
  kept.

**2. Video: HandBrake's own SVT-AV1 tuning.** HandBrake 1.11.2's built-in AV1
presets add
`enable-variance-boost=1:enable-qm=1:ac-bias=1:tf-strength=1:qp-scale-compress-strength=1:sharpness=1:keyint=10s`
with tune `psnr`; this preset has `lp=3` and, after import, tune `vq`
(*Video encoder* in the doc). Test on a clip of each kind: a grainy film DVD,
a clean Blu-ray, and animation. Compare size and appearance at the same CRF,
and against the current preset. Variance boost makes files larger at a given
CRF, so the CRF may need moving with it.

**3. Video: quality by source.** CRF 30 is near the lower-quality end of
HandBrake's SVT-AV1 range for DVDs (RF 22–32) and mid-range for 1080p (25–35);
*Downton Abbey*'s 1080p picture came out at 0.81 Mb/s. `HB_PRESET_DVD` and
`HB_PRESET_BD` can name different presets, so a DVD preset at a lower CRF costs
one more preset in the ConfigMap. Settle it by watching test clips on the
largest screen they will be seen on. Disc size matters less than it seems
while audio is most of the file (item 1).

**4. Video: film grain synthesis.** `film-grain=8` suits live action with
normal grain (SVT-AV1's advice) and shrinks grainy films most, but would put
grain on animation, which gets 4. One preset can't tell them apart. Worth a
test on a grainy DVD, and on animation, before deciding whether the gain
justifies the mismatch.

**5. Frame rate: hard-telecined DVDs.** Job 34's transcode is 29.97 fps
with decombed frames where the source has a pulldown pattern (*Frame rate and
interlacing* in the doc). Turning `PictureDetelecine` on helps such discs and
can harm truly interlaced ones (concert and TV discs). Test it on job 34's raw
copy, and on an interlaced TV disc, before choosing.

**6. The preset file itself.**

- Fix `PresetDescription`, which claims TrueHD and DTS-HD MA passthrough that
  doesn't happen.
- Re-export the preset from HandBrake 1.11.2 so the file says what runs: it
  comes out as format version 72 with tune `vq` and the other keys import adds
  written down, and without `MetadataPassthrough` and `VideoQSVDecode`
  (verified 2026-11-16). The unused manual crop values survive the export,
  and would go by hand. Export with `HandBrakeCLI --preset-import-file <file>
  --preset <name> --preset-export <name> --preset-export-file <out>`.

**Not proposed:** the clean-up filters, crop, pixel aspect, container,
chapters, subtitles and `lp=3` are as good practice has them (the doc says why
for each).

## Open questions

- **Which Plex clients play AV1 and Opus directly.** A client that can't decode
  AV1 makes Plex re-encode the video on the server for every play, and
  multichannel Opus support varies by device. Plex publishes no list for
  either. Nothing reelbarrow has filed in AV1 has been played yet, so the
  answer is in Plex's dashboard once one is: whether a play shows *Direct Play*
  or *Transcode*.
- **7.1 sources.** The 5.1 entry downmixes them. Whether any disc here has 7.1
  hasn't been checked.

## How to test a change

Transcode two minutes of a raw copy in ARM's pod while ARM is idle, writing to
the container's `/tmp` rather than the share, with the candidate preset
imported from a file copied there:

```bash
HandBrakeCLI -i <raw MKV or BDMV folder> -o /tmp/test.mkv \
  --preset-import-file /tmp/candidate.json --preset <name> \
  --subtitle-default=none --start-at seconds:600 --stop-at seconds:120
```

Compare against the same two minutes under the current preset: size per
stream (`ffprobe -show_entries packet=stream_index,size`), frame rate, the
audio tracks and names, and the picture by eye, ideally on the TV the library
is watched on. Then `kubectl cp` the clips off to watch them.

## Done when

The changes Will chooses are in the ConfigMap, ARM has restarted, and a disc
ripped after shows them in its transcode, measured with ffprobe on the share.
`docs/handbrake-preset.md` is updated to match and this spec deleted.
