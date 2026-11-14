# scale-bridge (stacks/scale-bridge): Body Pod 2 BLE scale -> wger, plus Lefu's
# 8-electrode body-composition engine as a child program (README "Engine").
#
# Stage `engine` (target arch; on the Pi arm64 natively): download the pinned engine
# inputs and check every sha256 (engine/deps.lock: Maven Central jars, no Maven;
# engine/sdk.lock: Lefu SDK .aar files from Lefu's public GitHub at a pinned commit),
# rewrite the vendor jars' native methods (ASM), compile the bridge classes, jlink a
# small JRE. Nothing vendor-owned is in the repo. NO SECRET in any stage, ARG or layer:
# the engine reads LEFU_SECRET_8 from its environment at run time only.
FROM eclipse-temurin:21.0.13_9-jdk-noble@sha256:06c1f157dacf6b68198bd9a4186dd4c34ef24e65d7bbbe2a0cf8e62571ad38c9 AS engine
ARG TARGETARCH
WORKDIR /src
COPY engine/fetch.sh engine/deps.lock engine/sdk.lock ./
RUN sh fetch.sh deps deps.lock /deps && sh fetch.sh sdk sdk.lock /sdk
COPY engine/patcher/ patcher/
COPY engine/src/ src/
RUN set -eu; \
    case "$TARGETARCH" in amd64) U=linux_64; J=linux-x86-64;; arm64) U=linux_arm64; J=linux-aarch64;; *) echo "unsupported arch $TARGETARCH" >&2; exit 1;; esac; \
    mkdir -p /out/lefu/lib /out/lefu/native "/out/lefu/res/$J" /patcher /classes /vendor /x/u /x/j /x/k /x/c; \
    javac -nowarn --release 21 -d /patcher -cp /deps/build/asm-9.8.jar patcher/NativePatch.java; \
    for j in /sdk/jars/*.jar; do java -cp /deps/build/asm-9.8.jar:/patcher NativePatch "$j" "/vendor/$(basename "$j")" 2>/dev/null; done; \
    javac -nowarn --release 21 -d /classes -cp "$(ls /deps/runtime/*.jar /vendor/*.jar | tr '\n' ':')" $(find src -name '*.java'); \
    jar --create --file /out/lefu/lib/lefu-engine.jar -C /classes .; \
    cp /deps/runtime/*.jar /vendor/*.jar /out/lefu/lib/; \
    cp -R /sdk/jni /sdk/assets /out/lefu/; \
    # Unicorn 1 then Unicorn 2 natives into one folder (Unicorn 2's libunicorn.so wins, as in the prototype)
    (cd /x/u && jar xf /deps/runtime/unicorn-1.0.14.jar "natives/$U" && jar xf /deps/runtime/unidbg-unicorn2-0.9.8.jar "natives/$U"); \
    (cd /x/j && jar xf /deps/runtime/jna-5.10.0.jar "com/sun/jna/$J"); \
    (cd /x/k && jar xf /deps/runtime/keystone-0.9.7.jar "$J"); \
    (cd /x/c && jar xf /deps/runtime/capstone-3.1.8.jar "$J"); \
    cp "/x/u/natives/$U/libunicorn_java.so" "/x/u/natives/$U/libunicorn.so" "/x/j/com/sun/jna/$J/libjnidispatch.so" /out/lefu/native/; \
    # keystone/capstone load through JNA's extractFromResourcePath, which copies a jar resource to
    # a temp file and dlopen()s it (fails on a noexec /tmp). Found in a plain classpath folder,
    # the file is used in place.
    cp "/x/k/$J/libkeystone.so" "/x/c/$J/libcapstone.so" "/out/lefu/res/$J/"; \
    commit=$(sed -n 's/^COMMIT=//p' sdk.lock); \
    sdk=$(grep -oE '[^/]+\.aar$' sdk.lock | sed 's/\.aar$//' | sed 's/.*/"&"/' | paste -sd, -); \
    printf '{"sdk_repo":"LefuHengqi/PPBaseKit-Android","sdk_commit":"%s","sdk":[%s],"unidbg":"0.9.8","backend":"unicorn2","arch":"%s"}\n' \
      "$commit" "$sdk" "$TARGETARCH" > /out/lefu/engine-info.json; \
    jlink --add-modules java.base,java.desktop,java.management,java.sql,java.logging,java.xml,jdk.unsupported \
      --strip-debug --no-header-files --no-man-pages --compress=zip-6 --output /out/jre; \
    ls /out/lefu/jni/arm64-v8a /out/lefu/native "/out/lefu/res/$J" >&2

# ---- runtime: the bridge (Python) + the engine (JRE + jars + natives) ----
FROM python:3.12.16-slim@sha256:5e2bdb1b0d542d5039cffb8181c65930093949194a65c365dfb1b0fe13a4fa5b
# Talks to the host's bluetoothd over the D-Bus system bus (bind-mounted by compose);
# needs no Bluetooth packages in the image. Dependencies via uv from the committed
# uv.lock (owner 2026-11-01: use uv). Every dependency has an aarch64 wheel except
# pyric (pure Python, built from its sdist).
COPY --from=ghcr.io/astral-sh/uv:0.13.8@sha256:5cc1805e2426484abca4ad46ea783bab281fdb225fd82d8ce6d66a81254bac85 /uv /usr/local/bin/uv
WORKDIR /app
COPY pyproject.toml uv.lock /app/
ENV UV_LINK_MODE=copy UV_COMPILE_BYTECODE=1 UV_PYTHON_DOWNLOADS=never
RUN uv sync --frozen --no-dev --no-install-project && rm /usr/local/bin/uv
ENV PATH="/app/.venv/bin:$PATH"
COPY --from=engine /out/jre /opt/jre
COPY --from=engine /out/lefu /opt/lefu
COPY --chmod=0755 engine/lefu-engine.sh /usr/local/bin/lefu-engine
ENV LEFU_LIB_DIR=/opt/lefu/jni/arm64-v8a LEFU_ASSETS=/opt/lefu/assets LEFU_PACKAGE=com.lefu.demo
COPY scale_bridge/ /app/scale_bridge/
# -I (isolated mode) drops the working dir from sys.path; a .pth file in the venv's
# site-packages is still honoured (same fix as stacks/grocery-planner).
RUN echo /app > /app/.venv/lib/python3.12/site-packages/kdk-scale-bridge.pth \
    && python -I -c "import scale_bridge.ble, scale_bridge.app, scale_bridge.engine"
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
# Non-root; nothing in the image is writable by this uid (compose adds read_only).
# BlueZ's D-Bus policy lets any uid send to org.bluez (default context), but the
# host's dbus-daemon only accepts a connection from a uid that resolves on the
# host: 65534 (nobody:nogroup on Debian). 65532 has no host entry -> the bus
# resets the connection (ConnectionResetError, Errno 104).
USER 65534:65534
# The main loop touches this file at least every 30 s (and around every session).
HEALTHCHECK --interval=60s --timeout=5s --start-period=60s --retries=3 \
    CMD ["python", "-I", "-c", "import os,sys,time; sys.exit(0 if time.time() - os.path.getmtime('/tmp/scale-bridge.alive') < 300 else 1)"]
ENTRYPOINT ["python", "-I", "-m", "scale_bridge"]
