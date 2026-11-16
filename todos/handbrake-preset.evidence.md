# handbrake-preset: evidence

Measurements behind [handbrake-preset.md](handbrake-preset.md) and
`docs/handbrake-preset.md`. All read-only. Captures are in reelbarrow's checkout,
`captures/handbrake-preset/` (gitignored).

## 2026-11-16: what ARM's transcodes hold

Stream sizes, from ffprobe's packet sizes summed per stream, in the reelbarrow
pod over ARM's `completed/` (`stream-sizes.txt`):

| transcode | source | video | audio | subtitles | file |
|---|---|---|---|---|---|
| *Downton Abbey* (2019) | Blu-ray | 738 MB | 1,192 MB in 7 tracks: 6 stereo at ~160 MB, 5.1 at 223 MB | 158 MB, 3 picture tracks | 2,107 MB |
| *Shrek the Third* | DVD | 299 MB | 719 MB in 5 tracks: stereo downmix 131 MB, 5.1 193 MB, three stereo ~131 MB | 7 MB | 1,035 MB |
| job 34, *Monte Carlo or Bust!* | DVD | 1,338 MB | 477 MB: 178 MB and 299 MB, both stereo, from one stereo AC3 track | 0.06 MB | 1,822 MB |

Downton's video is 0.81 Mb/s at 1080p. Job 34's second audio track is 319 kb/s:
the 5.1 entry's bitrate on a stereo mixdown.

Audio tracks and titles (`streams.txt`): Pale Rider's and Shrek's stereo
downmixes are titled *Surround 5.1*; Field of Dreams' source has English, French
and a second English stereo track, and its transcode two English stereo tracks
from the first, then French, then the second English.

Frame rates: job 34's transcode is 29.97 fps; Pale Rider's, Field of Dreams' and
Shrek's are 23.976, from sources whose MPEG-2 is 29.97 with soft pulldown.
`ffmpeg -vf idet` over 60 s at 15 min and 60 min of job 34's raw MKV
(`idet-job34.txt`): repeated fields in 466 and 323 of 1,799 frames (top and
bottom about equal); single-frame detection TFF 1,288 and 1,402, progressive
247 and 253.

## 2026-11-16: what HandBrake runs

HandBrake 1.11.2 (nixpkgs, as ARM's image) with the preset from the ConfigMap,
on an ffmpeg-made MKV; its logged job JSON:

- Video: `svt_av1_10bit`, preset 4, quality 30, **tune `vq`**, options `lp=3`,
  `PasshtruHDRDynamicMetadata` 6 (all). SVT-AV1 reports **v4.1.0**, *"Level of
  Parallelism: 3"*. HandBrake 1.11.2's `contrib/svt-av1/module.defs` fetches
  SVT-AV1 v4.1.0, and ARM's own `/usr/local/bin/HandBrakeCLI` links it
  statically, its source paths in the binary reading `SVT-AV1-v4.1.0/Source/…`;
  Opus is the system `libopus.so.0`.
- Audio, from one mono source track: two outputs, mono 192 kb/s at −3 dB, and
  mono 256 kb/s (the 5.1 entry, capped for mono). The duplicate is the same
  mechanism as job 34's.
- Filters: comb detect, decomb mode 7, frame-rate shaper (VFR), crop and scale.
  No detelecine.

`HandBrakeCLI --preset-import-file preset.json --preset vulcanus-handbrake-preset
--preset-export vulcanus-handbrake-preset --preset-export-file out.json` writes
the preset as HandBrake holds it after import: `VersionMajor` 72, `VideoTune`
`vq`, `MetadataPassthru` true, `VideoPasshtruHDRDynamicMetadata` `all`,
`AudioTrackNamePassthru` true, no `MetadataPassthrough` or `VideoQSVDecode`,
and `PictureTopCrop` still 22.

HandBrake 1.11.2's import upgrades (`libhb/preset.c`): `import_svt_av1_tune_61_0_0`
sets an empty SVT-AV1 tune to `vq`; `import_passthru_preset_settings_57_0_0`
copies `MetadataPassthrough` to `MetadataPassthru`;
`import_dynamic_metadata_preset_settings_60_0_0` sets HDR dynamic metadata to
`all`; `import_track_names_preset_settings_64_0_0` turns on track name passthrough.

`add_audio_for_lang` in `libhb/preset.c` creates one output per `AudioList`
entry for each source track, the first entry only for secondary tracks when
`AudioSecondaryEncoderMode` is set, and skips a source track only when the same
entry already used it; nothing compares outputs with each other.
`sanitize_audio_codec` keeps an explicit encoder, so `AudioCopyMask` only
matters to `copy` entries.

## 2026-11-16: HandBrake's built-in presets (1.11.2)

From `preset/preset_builtin.json`, over all 106: `PictureDetelecine` `off` in
all; `PictureDeinterlaceFilter` `decomb` in 65, `off` in 41; denoise and
deblock `off` in all; `PictureCropMode` 0 (automatic) in 76, 2 (none) in 28;
`VideoFramerateMode` `pfr` in 76.

Their SVT-AV1 presets: tune `psnr`, options
`enable-variance-boost=1:enable-qm=1:ac-bias=1:tf-strength=1:qp-scale-compress-strength=1:sharpness=1:keyint=10s`
(PR #7286, merged 2026-01-24, which also lowered their quality values so files
came out smaller than the x264 presets'). *HQ 2160p60 4K AV1 Surround*: preset
4, quality 30.5; *Super HQ*: preset 3, quality 25; *Matroska/AV1 MKV 2160p60
4K*: preset 4, quality 35. Their audio: AAC or Opus stereo at 160 kb/s, gain 0,
with AC3 passed through in the surround presets.

## Sources read

- SVT-AV1 v4.1.0 `Docs/Parameters.md`, `Docs/CommonQuestions.md`,
  `Docs/Appendix-Variance-Boost.md`, `Docs/Appendix-Film-Grain-Synthesis.md`
  (gitlab.com/AOMediaCodec/SVT-AV1, tag v4.1.0).
- HandBrake's documentation source, github.com/HandBrake/handbrake-docs,
  `source/docs/en/latest/`: `workflow/adjust-quality`, `technical/frame-rates`,
  `technical/filters-summary`, `technical/video-presets-tunes`; its audio pages
  are placeholders.
- Xiph's *Opus Recommended Settings*, through web.archive.org, as
  wiki.xiph.org's certificate had expired.
- Plex's *What media formats are supported?* lists some devices only; no
  device list for AV1 or Opus was found.
