# insta360-flat

Nightly one-shot on **kdk-dkr-04**: raw Insta360 X4 trips on the NAS
(`/mnt/insta360-upload`, read-only) → finished flat stills in
`/mnt/insta360-flat/<YYYY-MM>-<trip>/` (read-write), which Immich on kdk-dkr-dmz-01
indexes as a read-only external library.

- `flatten.py` (stdlib, bind-mounted): the laptop pipeline in `~/cad/tools/imaging`
  (`process-one.sh`, `level.py`, `insp_trailer.py`, `flat-360.sh`, `touch.sh`, `slug.py`)
  in one file, same maths and same ffmpeg/PhotoCraft arguments.
- `Dockerfile`: python 3.12 slim (digest-pinned, Renovate) + BtbN static FFmpeg n9.0.2 and
  PhotoCraft CLI 0.23.1, both by exact release + sha256 (bumped by hand). The image tag in
  `compose.yaml` names both versions; bump it together with the ARGs.
- Komodo: stack + Procedure `insta360-flat`, 02:35 nightly, `deploy = false`,
  `destroy_before_deploy`, pre_deploy mount guard (upload `ro`, flat `rw`) + pushover copy.
- Hardened: read-only rootfs, `cap_drop: ALL`, no-new-privileges, uid 3003:3001, tmpfs
  `/tmp` (2 GB), `mem_limit` 6g, `pids_limit` 512, `cpus` 10.

Manual run on the host (same as the Procedure):

```bash
ssh kdkadmin@10.1.20.22 'cd /opt/kdk-lab/stacks/insta360-flat/stacks/insta360-flat && sudo docker compose run --rm flatten'
```

Docs: `docs/services/insta360-flat.md` (service), `docs/runbooks/imaging.md` (add a trip,
NAS objects, validation, limits).
