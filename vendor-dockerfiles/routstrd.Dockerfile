# Override for vendor/routstrd/Dockerfile.
#
# Upstream's Dockerfile runs `bun run build && bun install -g . &&
# bun add -g @earendil-works/pi-coding-agent`. In this harness:
#   1. `better-sqlite3` is an OPTIONAL native dependency of @routstr/sdk
#      (via applesauce-sqlite). `bun install --frozen-lockfile` on the slim
#      image skips building it (no toolchain), so `bun build` can't resolve it
#      AND the daemon can't `require` it at runtime. We install the build
#      toolchain and add better-sqlite3 explicitly so the native module is
#      present at runtime, and mark it `--external` so `bun build` emits a
#      runtime require (native .node can't be bundled) instead of failing.
#   2. The global installs (`-g .`, pi-coding-agent) are unneeded — compose
#      runs the built artifact directly via `bun run dist/daemon/index.js`.
#
# Context is vendor/routstrd (set in compose.yml).
FROM oven/bun:1-slim
WORKDIR /app

RUN apt-get update \
 && apt-get install -y --no-install-recommends python3 make g++ ca-certificates \
 && rm -rf /var/lib/apt/lists/*

COPY package.json bun.lock ./
RUN bun install --frozen-lockfile || bun install
# Ensure the native sqlite backend is actually built and present at runtime.
RUN bun add better-sqlite3

# Wallet backend: the daemon's wallet ops (receive/balance/send cashu) shell out
# to the `cocod` cashu daemon, which `routstr start` normally auto-installs.
# Running the bundle directly skips that, so install it here and put bun's
# global bin on PATH. createCocodClient auto-spawns `cocod init` on first use.
RUN bun install --global @routstr/cocod
ENV PATH="/root/.bun/bin:${PATH}"

COPY . .

ENV SQLITE_EXTERNALS="--external better-sqlite3 --external @libsql/client --external @tursodatabase/database --external @tursodatabase/database-wasm"
RUN mkdir -p dist/daemon \
 && bun build src/index.ts        --target=bun --outfile=dist/index.js        $SQLITE_EXTERNALS \
 && bun build src/daemon/index.ts --target=bun --outfile=dist/daemon/index.js $SQLITE_EXTERNALS

# Isolate Nostr discovery to the LOCAL relay only. The @routstr/sdk discovery
# adapter hardcodes public relays (nos.lol, relay.damus.io, relay.primal.net,
# relay.routstr.com) with no env override — so an unpatched daemon discovers
# the global routstr network and would route paid requests to public nodes.
# Rewriting those URLs to ws://relay:8080 (the compose `relay` service) makes
# routstrd discover ONLY node-a / node-b, which announce on that relay.
RUN sed -i \
  -e 's#wss://nos.lol#ws://relay:8080#g' \
  -e 's#wss://relay.damus.io#ws://relay:8080#g' \
  -e 's#wss://relay.primal.net#ws://relay:8080#g' \
  -e 's#wss://relay.routstr.com#ws://relay:8080#g' \
  dist/index.js dist/daemon/index.js

EXPOSE 8008
CMD ["/bin/bash"]
