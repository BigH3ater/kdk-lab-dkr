# Actual Budget bank-sync one-shot. Node 24, @actual-app/api 26.10.0 (matches the
# actual-server 26.10.0 image). Two stages so the runtime image carries no build
# toolchain: the builder (full image, has python/make/g++) installs deps from the
# committed lockfile -- better-sqlite3 uses its bundled linux-x64 glibc prebuild,
# but the full image is a safe fallback if it ever has to compile -- and the slim
# runtime just runs the prebuilt .node. Renovate keeps both tags + digests in sync.

FROM node:24.22.2-bookworm@sha256:e7add2a490bb17d97b485b8641bd88455fff7839ade0f5eba493c3508124d413 AS build
WORKDIR /app
COPY package.json package-lock.json ./
# Exact, reproducible install from the lockfile; dev deps (none shipped) excluded.
RUN npm ci --omit=dev --no-audit --no-fund

FROM node:24.22.2-bookworm-slim@sha256:c86ecead9e55a5210ac1727de38a4ca840bf03dcaf34dc9749addbf383b1920c
# HOME on tmpfs (/tmp) so a read-only rootfs + non-root user has somewhere to write.
ENV NODE_ENV=production \
    HOME=/tmp \
    NODE_OPTIONS=--disable-proto=throw
WORKDIR /app
COPY --from=build /app/node_modules ./node_modules
COPY package.json package-lock.json lib.js banksync.js config-check.js ./
# node images ship a non-root `node` user at uid 1000.
USER 1000:1000
ENTRYPOINT ["node", "/app/banksync.js"]
