---
title: Jellyfin transcoding (ramdisk + Arc QSV)
---

# Jellyfin transcoding: RAM disk + Arc QSV settings

Jellyfin runs on `kdk-dkr-dmz-01` with an Intel **Arc A380** (DG2) for Quick
Sync (QSV) transcoding. Two things make transcoding fast and correct: a RAM-disk
scratch for the transcode segments, and the QSV/HDR encoding settings tuned for
Arc. Both are documented here because both had non-obvious gotchas.

See also the service page [[jellyfin]].

## The transcode RAM disk

Transcode segments are ephemeral (HLS chunks written, served, discarded), so
they belong in RAM — faster than the SSD and zero disk wear.

**The gotcha:** the ramdisk must be mounted at the path Jellyfin *actually*
writes to. On this version that is **`/config/cache/transcodes`** (the default
`TranscodingTempPath` derives from the cache dir), **not** `/config/data/transcodes`
(a stale dir from an older version) and **not** a standalone `/transcodes` (which
Jellyfin never touches — a tmpfs there is mounted but unused). Confirm the real
path from the ffmpeg logs:

```bash
ssh kdkadmin@192.168.191.20
sudo grep -hoE '/config/(cache|data)/transcodes' \
  /opt/kdk-lab/jellyfin/config/log/FFmpeg.Transcode-*.log | sort | uniq -c
```

The `stacks/jellyfin/compose.yaml` mount:

```yaml
    tmpfs:
      # mode=1777 (sticky, world-writable like /tmp) is REQUIRED: the tmpfs
      # otherwise inherits the pre-existing dir's 2755 root:root and Jellyfin
      # (PUID 1000) can't write -> transcodes fail.
      - /config/cache/transcodes:size=6g,mode=1777
```

Why `mode=1777`: a tmpfs mounted over an existing directory inherits that
directory's perms (here `2755 root:root`), which Jellyfin's `abc`/PUID 1000 user
cannot write to — transcoding would fail with *permission denied*. Forcing
`1777` (sticky world-writable, like `/tmp`) fixes it.

**Sizing:** 6 GB. The DMZ host has 23 GB RAM (only an idle 12 GB `/tmp` tmpfs
besides), so 6 GB is safe. Pair it with segment deletion (below) so a long 4K
transcode can't fill it.

### Verify the ramdisk

```bash
ssh kdkadmin@192.168.191.20
# is the real transcode path a tmpfs?
sudo docker exec jellyfin sh -c 'df -h /config/cache/transcodes | tail -1'   # -> tmpfs 6.0G
# writable by the Jellyfin user (not just root)?
sudo docker exec -u abc jellyfin sh -c 'touch /config/cache/transcodes/.w && echo OK && rm -f /config/cache/transcodes/.w'
# during playback, segments should appear and RAM use climb:
sudo docker exec jellyfin sh -c 'ls /config/cache/transcodes | wc -l; df -h /config/cache/transcodes | tail -1'
```

## Arc QSV encoding settings

Hardware accel is QSV on `/dev/dri/renderD128` (iHD driver). Settings live in
**`/opt/kdk-lab/jellyfin/config/encoding.xml`** — this is host state under
`/config`, **not** in git, so back it up before editing and re-apply after any
config reset. The values below are the validated best-practice set for the A380:

| `encoding.xml` setting | Value | Why |
|---|---|---|
| `HardwareAccelerationType` | `qsv` | Arc QSV via VAAPI; full-GPU decode→scale→encode |
| `VaapiDevice` | `/dev/dri/renderD128` | the A380 render node |
| `EnableHardwareEncoding` | `true` | use the GPU encoder |
| `EnableVppTonemapping` | `true` | **HDR→SDR tone-map** in the QSV/VPP pipeline. Without it, HDR sources are only relabeled bt709 and play washed-out/grey |
| `AllowAv1Encoding` | `true` | the A380's standout feature; big bitrate savings to AV1-capable clients |
| `AllowHevcEncoding` | `true` | HEVC for capable clients |
| `HardwareDecodingCodecs` | h264, hevc, mpeg2video, vc1, vp9, **av1** | Arc decodes all of these in hardware |
| `EnableIntelLowPowerH264HwEncoder` | `true` | Arc VDENC/LP path — more efficient, more concurrent streams |
| `EnableIntelLowPowerHevcHwEncoder` | `true` | same for HEVC |
| `EnableSegmentDeletion` | `true` | prune old HLS segments so the 6 GB RAM disk can't fill on long 4K transcodes |
| `EnableDecodingColorDepth10Hevc` | `true` | 10-bit HEVC decode (most 4K remuxes) |

### Editing encoding.xml safely

Jellyfin rewrites `encoding.xml` when settings change in the UI, and can
overwrite a hand-edit on shutdown. Always **stop the container first**:

```bash
ssh kdkadmin@192.168.191.20
sudo docker stop jellyfin
sudo cp /opt/kdk-lab/jellyfin/config/encoding.xml \
        /opt/kdk-lab/jellyfin/config/encoding.xml.bak
sudo sed -i 's#<EnableVppTonemapping>false</EnableVppTonemapping>#<EnableVppTonemapping>true</EnableVppTonemapping>#' \
        /opt/kdk-lab/jellyfin/config/encoding.xml
# ... repeat per setting; validate it is still well-formed XML ...
```

Then redeploy so Jellyfin re-reads it (equivalent settings are also in the
dashboard under **Playback → Transcoding**):

```bash
# from a compose host with the Komodo API creds (op://kdk-ops/komodo-api-rw):
# DeployStack jellyfin   (see the Komodo GitOps runbook for the API call)
```

## Verify HW transcode, tone-mapping, and low-power

The ffmpeg command in the transcode log is ground truth. Resume/start a playback
that transcodes an **HDR** title, then:

```bash
ssh kdkadmin@192.168.191.20
f=$(sudo ls -t /opt/kdk-lab/jellyfin/config/log/FFmpeg.Transcode-*.log | head -1)
cmd=$(sudo grep -aoE '/usr/lib/jellyfin-ffmpeg/ffmpeg.*' "$f" | head -1)
echo "$cmd" | grep -q 'tonemap_vaapi' && echo 'HDR tone-mapping: ON'
echo "$cmd" | grep -q 'low_power 1'  && echo 'Low-power encode: ON'
echo "$cmd" | grep -oE '\-codec:v:0 [a-z0-9_]+'   # h264_qsv / hevc_qsv / av1_qsv
```

A correctly tone-mapped HDR chain reads the source as HDR and applies a curve —
`setparams=…color_trc=smpte2084…, scale_vaapi…, tonemap_vaapi=…:t=bt709…`. If
you instead see only `setparams=…bt709` with **no** `tonemap_vaapi`, tone-mapping
is off (washed-out output) — check `EnableVppTonemapping`.

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| Transcodes hit the SSD, not RAM | tmpfs mounted at the wrong path (`/transcodes` or `/config/data/transcodes`) | mount tmpfs at `/config/cache/transcodes` (verify from ffmpeg logs) |
| Transcode fails, *permission denied* on the scratch dir | tmpfs inherited `root:root` perms | add `,mode=1777` to the tmpfs mount |
| HDR content looks washed-out / grey | `EnableVppTonemapping=false` | set it `true`, redeploy |
| RAM disk fills on long 4K playback | segments never pruned | `EnableSegmentDeletion=true` (and size the tmpfs for headroom) |
| Playback shows `(hw)` gone → CPU transcode | `/dev/dri` missing (host on cloud kernel) | see [[jellyfin]] — host must run `linux-image-amd64` |
| Settings revert after a UI change | Jellyfin rewrote `encoding.xml` | re-apply; remember `/config` is host state, not git |
