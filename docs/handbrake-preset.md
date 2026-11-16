# HandBrake preset

ARM transcodes every title it keeps with one custom HandBrake preset,
`vulcanus-handbrake-preset`. It is the JSON file `vulcanus-handbrake-preset.json`
in ARM's ConfigMap (`kubernetes/apps/automatic-ripping-machine/config-map.yaml`),
loaded for DVDs and Blu-rays alike by `--preset-import-file` in `HB_ARGS_DVD`
and `HB_ARGS_BD`. This page explains every setting in it, grouped by what the
group is for: what the setting does, what is considered good practice, and
where this preset departs from it. How ARM runs the encode (one at a time, on
CPUs 1–5 of `piraeus-worker-1`) is in
[automatic-ripping-machine.md](automatic-ripping-machine.md).

Changes under consideration are [`todos/handbrake-preset.md`](../todos/handbrake-preset.md)'s.
The raw copy of every disc is kept beside its film, so any change can be applied
to past titles by transcoding again.

## What runs is not quite what the file says

ARM's image runs **HandBrake 1.11.2**, which bundles **SVT-AV1 4.1.0**. The file
says `"VersionMajor": 53`, an older preset format, and HandBrake upgrades it on
import (`libhb/preset.c`, the `import_*` functions). The upgrade fills in what
version 53 lacked:

| key | value after import | effect |
|---|---|---|
| `VideoTune` | `vq` (the file has `""`) | SVT-AV1's visual-quality tune; see *Video encoder* |
| `MetadataPassthru` | from the file's `MetadataPassthrough` | the old key's value carried to the new name |
| `VideoPasshtruHDRDynamicMetadata` | `all` | HDR10+ and Dolby Vision metadata kept where a source has it |
| `AudioTrackNamePassthru`, `SubtitleTrackNamePassthru` | `true` | each output track takes its source track's name; see *Audio* |
| `AudioAutomaticNamingBehavior` | `unnamed` | tracks without a source name get none |
| `VideoColorRange` | `limited` (the template's default) | studio range, as every disc is |

HandBrake logs the job it actually runs, as JSON, at the top of each encode
(`json job:`). That log, not the preset file, is the authority on what a
transcode was made with. `VideoQSVDecode` in the file is a key no current
HandBrake reads.

## Video encoder and rate control

The picture is encoded as AV1 by SVT-AV1, at a constant quality.

| setting | value | what it does |
|---|---|---|
| `VideoEncoder` | `svt_av1_10bit` | SVT-AV1, 10 bits per sample even from 8-bit sources |
| `VideoQualityType` | `2` | constant quality (CRF), not a target bitrate |
| `VideoQualitySlider` | `30` | the CRF; lower is higher quality and larger |
| `VideoPreset` | `4` | SVT-AV1's speed/efficiency trade, 0 slowest to 13 fastest |
| `VideoTune` | `vq` after import | the rate-distortion target: visual quality rather than PSNR |
| `VideoOptionExtra` | `lp=3` | options passed to SVT-AV1 as written |
| `VideoProfile`, `VideoLevel` | `auto` | chosen by the encoder from the stream |
| `VideoMultiPass`, `VideoTurboMultiPass` | `false` | one pass |
| `VideoAvgBitrate` | `15000` | unused: only read when `VideoQualityType` is a bitrate |
| `VideoHWDecode` | `0` | sources decoded on the CPU |
| `VideoColorMatrixCodeOverride` | `0` | colour tags taken from the source |

**Good practice, and where this preset stands:**

- **Constant quality is right** for a library: HandBrake's own advice is
  *"Always use constant quality unless you have a specific reason not to"*
  (`workflow/adjust-quality` in HandBrake's docs), and SVT-AV1 says multi-pass
  *"is not critical in most situations"* in CRF mode
  (`Docs/CommonQuestions.md`). One pass at a CRF is the accepted approach.
- **10-bit AV1 from 8-bit sources** is the common choice: it reduces banding
  in gradients for little cost, and every AV1 decoder handles Main profile
  10-bit.
- **Preset 4** sits in the range SVT-AV1 calls *"commonly used by home
  enthusiasts as they represent a balance of efficiency and reasonable
  compute time"* (presets 4–6), with the advice to *"use the lowest preset
  that is tolerable"*. A 1080p Blu-ray encodes at about 13 fps here, so a
  two-hour film takes nearly four hours.
- **CRF 30.** HandBrake recommends SVT-AV1 RF 22–32 for 480p/576p DVDs and
  25–35 for 1080p. 30 is in the middle for Blu-ray and near the low-quality end
  for DVD. HandBrake's own reasoning is that lower resolutions need *lower*
  numbers, since a DVD is enlarged several times to fill a screen and its
  flaws with it. The same CRF is used for both because `HB_PRESET_DVD` and
  `HB_PRESET_BD` name one preset.
- **Tune and psycho-visual options depart from HandBrake's own AV1 presets.**
  HandBrake 1.11.2's built-in SVT-AV1 presets (*HQ 2160p60 4K AV1 Surround*,
  *Matroska/AV1 MKV 2160p60 4K* and the rest) set tune `psnr` with
  `enable-variance-boost=1:enable-qm=1:ac-bias=1:tf-strength=1:qp-scale-compress-strength=1:sharpness=1:keyint=10s`,
  added by HandBrake PR #7286 once SVT-AV1 3.2 fixed the artefacts
  quantisation matrices caused. Those are the SVT-AV1 features meant for
  perceived quality: variance boost raises the quality of low-variance
  blocks, which otherwise blur in *"clouds, skin, and delicate textures"* and
  in *"night, foggy, and overexposed shots"* (`Docs/Appendix-Variance-Boost.md`);
  quantisation matrices weight quantisation across frequencies, as other
  modern encoders do; `ac-bias` preserves texture and grain energy
  (*"moderate values (1.0–1.5) help retain sharpness and acuity of textures"*,
  `Docs/Parameters.md`); `tf-strength=1` weakens the temporal filter that
  smooths detail on reference frames. This preset uses none of them.
- **No film grain synthesis.** SVT-AV1 can remove grain before encoding and
  re-create it at playback from a small model, which its maintainers say
  *"can greatly improve efficiency while retaining visual quality and
  character"*: about `film-grain=8` for live action with normal grain, 10–15 for
  noisy sources, 4 for hand-drawn animation (`Docs/CommonQuestions.md`). It
  is off by default because it suits only grainy sources, and one preset here
  serves animation and live action alike.
- **`lp=3`** is SVT-AV1's *level of parallelism* (0–6, 0 choosing from the
  core count). It sets how many threads and pictures in flight the encoder
  uses, and so its memory; it *"does not indicate the number of threads
  targeted, nor does it constrain the encoder to run on a certain number of
  logical processors"* (`Docs/Parameters.md`). CPUs 1–5 run about 80% busy
  during an encode, so level 3 nearly fills the five CPUs the job is given.
- **Keyframes.** HandBrake sets none of SVT-AV1's GOP options, so a keyframe
  falls about every 5 s (SVT-AV1's `keyint` default), plus one at each chapter
  start, which HandBrake forces in CRF mode (`force_key_frames` in
  `libhb/encsvtav1.c`). HandBrake's own presets use 10 s. Either is fine for
  seeking.

## Frame rate and interlacing

How the source's frames become the output's: whether to keep its timing, and
what to do about interlaced or telecined DVD video.

| setting | value | what it does |
|---|---|---|
| `VideoFramerate` | `auto` | the source's frame rate |
| `VideoFramerateMode` | `vfr` | each frame keeps the source's timing, so mixed-rate sources stay mixed |
| `PictureDetelecine` | `off` | no inverse telecine |
| `PictureCombDetectPreset` | `default` | finds frames that show combing |
| `PictureDeinterlaceFilter` | `decomb` | deinterlaces the frames comb detection marks, and only those |
| `PictureDeinterlacePreset` | `default` | decomb's balanced mode |

**Good practice:** *Same as source* with variable frame rate makes the output
*"an exact copy of your Source frame rate. No frame rate conversion is
performed"* (`technical/frame-rates`). HandBrake's built-in presets use peak
frame rate instead, to cap high-frame-rate phone video for devices. That
matters for a 120 fps phone clip, not a disc. Decomb with comb detection is the
built-ins' choice too: it leaves progressive frames alone and repairs only
combed ones.

**Where this preset departs:** a film on an NTSC DVD is usually 23.976 fps
pictures turned into 29.97 fps video by 3:2 pulldown, either signalled by flags
(*soft* telecine) or baked into the video (*hard* telecine). HandBrake's decoder
undoes soft telecine by itself, which is why Pale Rider's, Field of Dreams' and
Shrek the Third's transcodes come out at 23.976. Hard telecine needs
`PictureDetelecine`. Without it, decomb deinterlaces the mixed frames, and the
output stays at 29.97, a fifth of its frames duplicates. HandBrake's built-in
presets all leave Detelecine off too. Detelecining video that is truly
interlaced does harm, and one preset can't know which a disc holds. Job 34 (*Monte
Carlo or Bust!*) is one such disc: its transcode is 29.97 fps, and ffmpeg's
`idet` finds 18–26% of its source frames repeating a field and 70–80% combed.
That is a pulldown pattern, though not a clean one, so whether Detelecine
improves it needs a test encode.

## Dimensions

How the picture is cropped and whether it is scaled.

| setting | value | what it does |
|---|---|---|
| `PictureCropMode` | `0` | automatic crop: black borders detected and removed |
| `PictureAutoCrop` | `true` | older key for the same |
| `PictureTopCrop`, `PictureBottomCrop`, `PictureLeftCrop`, `PictureRightCrop` | 22, 22, 0, 0 | ignored: read only in crop mode 3, custom |
| `PicturePAR` | `auto` | keeps the source's pixel aspect ratio: anamorphic DVDs stay 720 wide with their shape in the stream's aspect flag |
| `PictureItuPAR` | `false` | the generic 720-wide DVD aspect, not the ITU 704-wide one |
| `PictureKeepRatio` | `true` | dimensions keep the display aspect |
| `PictureUseMaximumSize`, `PictureWidth`, `PictureHeight` | `true`, 7680, 4320 | the largest output allowed; no source reaches it |
| `PictureAllowUpscaling` | `false` | never enlarges |
| `PictureForceWidth`, `PictureForceHeight`, `PictureDARWidth` | 0, 0, 1920 | used only by custom anamorphic, which this isn't |
| `PictureModulus` | `2` | dimensions divisible by 2, which 4:2:0 needs |
| `VideoScaler` | `swscale` | the scaler, idle since nothing is scaled |
| `PicturePadMode` and the `PicturePad*` keys | `none` | no padding |
| `PictureRotate` | `angle=0:hflip=0` | no rotation |

**Good practice:** keep the source's resolution and pixel aspect, never
upscale, and crop only black. This preset does all of that, as HandBrake's
built-ins do (76 of its 106 use automatic crop). Automatic crop has one known
weakness: a film that opens on a long dark scene can be over-cropped, and
crop mode 1, *loose*, crops more conservatively. The odd sizes it yields,
such as Shrek the Third's 720×478, are harmless to AV1, which has no
block-size requirement beyond the modulus.

## Clean-up filters

Filters that change the picture's content.

| setting | value |
|---|---|
| `PictureDenoiseFilter` (with `PictureDenoisePreset`, `PictureDenoiseTune`, `PictureDenoiseCustom`) | `off` |
| `PictureDeblockPreset` (with `PictureDeblockTune`, `PictureDeblockCustom`) | `off`; the tune and custom strings are unused |
| `PictureChromaSmoothPreset` (and its tune and custom) | `off` |
| `PictureSharpenFilter` (and its preset, tune and custom) | `off` |
| `PictureColorspacePreset` (and custom) | `off`: colours kept as the source has them |
| `VideoGrayScale` | `false` |

**Good practice:** off, as in every one of HandBrake's built-in presets. Each
trades detail for something else, and is for a source with a specific fault:
deblock for blocky MPEG-2, denoise for VHS or heavy noise. Grain in film is
better handled by the encoder's own synthesis (*Video encoder*) than by
denoising it away. Colorspace conversion is for tone-mapping HDR to SDR, which
nothing here wants.

## Audio

Which source tracks are kept, and what each becomes.

| setting | value | what it does |
|---|---|---|
| `AudioTrackSelectionBehavior` | `all` | every track whose language matches the list |
| `AudioLanguageList` | `["any"]` | every language |
| `AudioList` | two entries | each kept track's outputs: Opus **stereo** at 192 kb/s with −3 dB gain, then Opus **5.1** at 320 kb/s |
| `AudioSecondaryEncoderMode` | `true` | tracks after the first get only the first entry, Opus stereo |
| `AudioCopyMask` | `copy:truehd`, `copy:dtshd` | passthrough allowed for these, but only for an `AudioList` entry whose encoder is a passthrough (*auto passthru*); neither entry is, so nothing is passed through |
| `AudioEncoderFallback` | `opus` | the encoder when a passthrough isn't possible; unused for the same reason |
| per entry: `AudioMixdown` | `stereo`, `5point1` | the channel layout; HandBrake never upmixes, so a mixdown above the source's falls back to the source's |
| per entry: `AudioTrackGainSlider` | −3 dB, 0 dB | gain applied to that output |
| per entry: `AudioNormalizeMixLevel` | `false` | downmixes aren't scaled down to avoid clipping |
| per entry: `AudioTrackDRCSlider` | `0` | no dynamic range compression |
| per entry: `AudioSamplerate`, `AudioDitherMethod`, `AudioCompressionLevel`, `AudioTrackQuality`, `AudioTrackQualityEnable` | `auto`, `auto`, −1, 1 and 3, `false` | Opus always runs at 48 kHz; dither and compression level don't apply to it; bitrate, not quality, sets its size |

**Good practice:** Opus is the most efficient lossy codec for this, and Xiph
puts it at *"pretty much transparent"* at 128 kb/s for stereo, with 128–256 for
5.1 and 256–450 for 7.1 (Opus Recommended Settings, wiki.xiph.org). Keeping a
stereo downmix beside 5.1 is common, for players and rooms without surround,
and HandBrake's surround presets do it (AAC stereo plus the AC3 passed
through). Re-encoding lossless TrueHD and DTS-HD MA is a deliberate trade
here: the raw copy keeps the originals, and a lossless track alone can be
larger than the whole transcode.

**Where this preset departs, as measured on ARM's transcodes:**

- **Audio is most of each file.** The video stream is 35% of *Downton Abbey*'s
  2.1 GB and audio 57%, seven Opus tracks; Shrek the Third's audio is 70%.
  Every secondary track (other languages, commentaries) gets the 192 kb/s
  stereo entry, the same as the main one.
- **A stereo source becomes two stereo tracks.** The 5.1 entry can't upmix,
  so it falls back to stereo at 320 kb/s, beside the 192 kb/s stereo track:
  job 34's one stereo track became 178 MB and 299 MB of the same sound.
  HandBrake doesn't drop an output that duplicates another (`add_audio_for_lang`
  in `libhb/preset.c`).
- **Downmixes are named as their source.** With track names passed through, the
  stereo downmix of a 5.1 track keeps its source's title, so Pale Rider's and
  Shrek the Third's stereo tracks are titled *Surround 5.1*.
- **The −3 dB gain applies to every stereo output**, including those of stereo
  sources and every secondary track, where there is no downmix to protect from
  clipping. Those tracks play 3 dB quieter than the disc.
- **5.1 is the ceiling.** A 7.1 source is downmixed to 5.1.
- **The preset's description is wrong:** `PresetDescription` says *"TrueHD and
  DTS-HD MA passthrough"*; as above, nothing is passed through.

## Subtitles

| setting | value | what it does |
|---|---|---|
| `SubtitleTrackSelectionBehavior` | `all` | every track whose language matches |
| `SubtitleLanguageList` | `["eng", "any"]` | every language, English first |
| `SubtitleAddForeignAudioSearch` | `true` | scans picture subtitles for a track of forced lines, such as foreign dialogue, and adds it as a forced-only track |
| `SubtitleAddForeignAudioSubtitle` | `false` | no subtitle added when the audio itself is foreign |
| `SubtitleBurnBehavior`, `SubtitleBurnDVDSub`, `SubtitleBurnBDSub` | `none`, `false`, `false` | nothing burned into the picture |
| `SubtitleAddCC` | `false` | no separate closed-caption step; a DVD's captions still come through, as text, because selection is `all` |

**Good practice:** soft subtitles, all kept, in MKV, which carries both
DVD and Blu-ray picture subtitles and text. A player shows picture subtitles by
drawing them onto the video, which some Plex clients can't do without the
server re-encoding the picture; that is a reason to add text versions, not to
burn them in. The output tracks' default and forced flags are set by an
argument beside the preset (*Arguments beside the preset*).

## Container and metadata

| setting | value | what it does |
|---|---|---|
| `FileFormat` | `av_mkv` | Matroska |
| `ChapterMarkers` | `true` | the disc's chapters kept |
| `MetadataPassthrough` → `MetadataPassthru` | `true` | the source's global tags copied |
| `AlignAVStart` | `false` | no padding to align audio and video starts; an MP4-player workaround |
| `Optimize`, `Mp4iPodCompatible`, `InlineParameterSets` | `false` | MP4 and streaming options, unused in MKV |

**Good practice:** MKV, chapters and metadata, as here. Matroska is the one
container that holds everything a disc has: any number of audio tracks, picture
subtitles, chapters and tags.

## Arguments beside the preset

`HB_ARGS_DVD` and `HB_ARGS_BD` pass HandBrakeCLI two options of their own:

- `--preset-import-file /etc/arm/config/vulcanus-handbrake-preset.json`, which
  loads the preset; ARM adds `--preset vulcanus-handbrake-preset` from
  `HB_PRESET_DVD` and `HB_PRESET_BD`.
- `--subtitle-default=none`, which clears the default flag from every subtitle
  track the preset selects. HandBrake up to 1.11 flags every default subtitle
  track forced as well, and keeps the default MakeMKV puts on the first track,
  so without it a full English track would play over the whole film with
  subtitles turned off. The foreign-audio search's track isn't among those it
  clears, and stays default and forced, as a track of only forced lines
  should. Once ARM's image has a HandBrake release with its PR #8187, which
  stops forcing default tracks, the option can go. reelbarrow's design has the
  whole account (*Subtitle flags*).

## Bookkeeping

`PresetName`, `PresetDescription`, `PresetDisabled`, `Type` (1, custom), `Default`,
`Folder`, `FolderOpen` and `ChildrenArray` describe the preset to HandBrake's
preset list. `UsesPictureFilters` is `true`. `x264Option` and
`x264UseAdvancedOptions` belong to the x264 encoder and are unused.
