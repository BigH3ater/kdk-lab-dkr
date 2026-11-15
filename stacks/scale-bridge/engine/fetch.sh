#!/bin/sh
# Build stage only (Dockerfile, stage `engine`). Downloads the pinned engine inputs and
# checks every file's sha256; any mismatch or failed download fails the build.
#   fetch.sh deps <deps.lock> <outdir>   -> <outdir>/{runtime,build}/<name>.jar   (Maven Central)
#   fetch.sh sdk  <sdk.lock>  <outdir>   -> <outdir>/{jars,assets,jni/arm64-v8a}  (Lefu's GitHub, pinned commit)
#   fetch.sh sdk  <sdk.lock>  <outdir> --print-hashes      (bumping the SDK: print, do not check)
# Hosts contacted: repo1.maven.org, raw.githubusercontent.com. Extraction uses the JDK's `jar`.
set -eu
MODE=$1 LOCK=$2 OUT=$3 HASHES=${4:-}
get() { curl --proto '=https' --tlsv1.2 -fsSL --retry 3 --retry-delay 2 -o "$2" "$1"; }
check() { echo "$1  $2" | sha256sum -c --strict --status - || { echo "HASH MISMATCH: $3" >&2; exit 1; }; }

case "$MODE" in
deps)
  mkdir -p "$OUT/runtime" "$OUT/build"
  grep -E '^[0-9a-f]{64}  (runtime|build)  ' "$LOCK" | while read -r sum scope path; do
    f="$OUT/$scope/$(basename "$path")"
    get "https://repo1.maven.org/maven2/$path" "$f"
    check "$sum" "$f" "$path"
  done
  n=$(ls "$OUT/runtime" "$OUT/build" | grep -c '\.jar$')
  [ "$n" = "$(grep -cE '^[0-9a-f]{64}  ' "$LOCK")" ] || { echo "dependency count differs from $LOCK" >&2; exit 1; }
  ;;
sdk)
  REPO=$(sed -n 's/^REPO=//p' "$LOCK"); COMMIT=$(sed -n 's/^COMMIT=//p' "$LOCK")
  mkdir -p "$OUT/aar" "$OUT/jars" "$OUT/assets" "$OUT/jni/arm64-v8a" "$OUT/x"
  grep -E '^[0-9a-f]{64}  ' "$LOCK" | while read -r sum path; do
    f="$OUT/aar/$(basename "$path")"
    get "$REPO/$COMMIT/$path" "$f"
    if [ "$HASHES" = "--print-hashes" ]; then sha256sum "$f"; continue; fi
    check "$sum" "$f" "$path"
    name=$(basename "$path" .aar)
    rm -rf "$OUT/x" && mkdir "$OUT/x"
    (cd "$OUT/x" && jar xf "$f")
    mv "$OUT/x/classes.jar" "$OUT/jars/$name.jar"
    [ ! -d "$OUT/x/assets" ] || cp -R "$OUT/x/assets/." "$OUT/assets/"
    [ ! -d "$OUT/x/jni/arm64-v8a" ] || cp "$OUT/x/jni/arm64-v8a/"*.so "$OUT/jni/arm64-v8a/"
  done
  rm -rf "$OUT/x"
  ;;
*) echo "usage: fetch.sh deps|sdk <lock> <outdir> [--print-hashes]" >&2; exit 2 ;;
esac
