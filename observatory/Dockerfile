FROM python:3.12-slim

WORKDIR /app

# pdftotext, for the banks' Basel III rate-risk tables: those exist only
# inside each bank's own disclosure PDF (equity/irrbb_collect.py).
RUN apt-get update && apt-get install -y --no-install-recommends poppler-utils \
    && rm -rf /var/lib/apt/lists/*

# OpenAI's Codex CLI, for desks answered on their owner's ChatGPT plan
# (app/assistant/codex.py). Pinned by version and SHA-256: a changed download
# fails the build rather than shipping. Codex runs with its shell and every
# other built-in tool off; its only tools are ours. Also the research desk's
# AI tab (app/research_ai.py). 0.160 is the first release that lists the
# GPT-6 models for a ChatGPT plan.
ARG CODEX_VERSION=0.160.0
ARG CODEX_SHA256=306865417d4ee7a927785852910a527f41e1e159add390ac5ae3accb67d44a13
RUN python -c "import urllib.request; urllib.request.urlretrieve('https://github.com/openai/codex/releases/download/rust-v${CODEX_VERSION}/codex-x86_64-unknown-linux-musl.tar.gz', '/tmp/codex.tgz')" \
    && echo "${CODEX_SHA256}  /tmp/codex.tgz" | sha256sum -c - \
    && tar -xzf /tmp/codex.tgz -C /tmp \
    && install -m 0755 /tmp/codex-x86_64-unknown-linux-musl /usr/local/bin/codex \
    && rm -f /tmp/codex.tgz /tmp/codex-x86_64-unknown-linux-musl \
    && codex --version

# Codex's code-mode host, from the same release, next to codex where Codex
# looks for it. From 0.160 every ChatGPT-plan model but gpt-5.5 calls tools
# through it: model-written JavaScript in a V8 sandbox whose only built-ins
# are the tools we offer (no files, processes, network or imports).
ARG CODEX_HOST_SHA256=ac6cd6288f0e39f46a33eba1cfd37711651f14f33dbc73d6a1b82eb45e038cac
RUN python -c "import urllib.request; urllib.request.urlretrieve('https://github.com/openai/codex/releases/download/rust-v${CODEX_VERSION}/codex-code-mode-host-x86_64-unknown-linux-musl.tar.gz', '/tmp/host.tgz')" \
    && echo "${CODEX_HOST_SHA256}  /tmp/host.tgz" | sha256sum -c - \
    && tar -xzf /tmp/host.tgz -C /tmp \
    && install -m 0755 /tmp/codex-code-mode-host-x86_64-unknown-linux-musl /usr/local/bin/codex-code-mode-host \
    && rm -f /tmp/host.tgz /tmp/codex-code-mode-host-x86_64-unknown-linux-musl

# Anthropic's Claude Code, for the research desk's AI tab on a writer's own
# Claude plan (app/research_ai.py). The native binary, pinned by version and
# by the SHA-256 in Anthropic's release manifest for linux-x64. It runs with
# its built-in tools cut to web search and page fetch; the rest are ours.
ARG CLAUDE_CODE_VERSION=2.1.278
ARG CLAUDE_CODE_SHA256=5c4735937844e84f8a93306e841a5b0e12252909b07870f789b190468da147ab
RUN python -c "import urllib.request; urllib.request.urlretrieve('https://downloads.claude.ai/claude-code-releases/${CLAUDE_CODE_VERSION}/linux-x64/claude', '/tmp/claude')" \
    && echo "${CLAUDE_CODE_SHA256}  /tmp/claude" | sha256sum -c - \
    && install -m 0755 /tmp/claude /usr/local/bin/claude \
    && rm -f /tmp/claude \
    && DISABLE_AUTOUPDATER=1 claude --version

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY web ./web
# The Save to Plover browser extension: zipped per request for readers to
# download (app/assistant/reading.py). Left out, the download fails.
COPY extension ./extension
COPY seed ./seed
# The EDINET extractors run here now, not only on a laptop. They are what
# turns the capture jobs' archive into the served equity tables, and keeping
# them out of the image is exactly how the 5% filings went four weeks stale in
# August 2026 while the archive kept growing. Code and the filer registry
# only — the raw archive itself lives in the bucket.
COPY equity ./equity
# Hand-typed party curation. The live store is data/admin/parties.json on the
# volume; this is the git-committed copy, and it seeds an ABSENT store on a
# fresh volume. Left out of the image, the admin console comes up with zero
# profiles on a machine that has never had them — which is exactly what it did
# on the first deploy that shipped the registry.
COPY curation ./curation
COPY start.sh ./
RUN chmod +x start.sh

# data/ is a mounted volume in production: the DuckDB file and the raw
# source archive must survive redeploys.
ENV PYTHONUNBUFFERED=1

# The extractors default to a laptop's archive path, which does not exist
# here. Point them at the volume: only the daily-list cache and the bucket
# listing are written there (a few MB), never the filings themselves, which
# stay in the bucket and are streamed as needed.
ENV EDINET_ARCHIVE_ROOT=/app/data/edinet-cache
ENV EDINET_LISTING_CACHE=/app/data/edinet-cache/listing

# How deep the container digs each night. Every extractor resumes from a
# watermark and so only ever walks FORWARD: a database built forward from the
# day an extractor was written stays as shallow as its first run, however deep
# the archive behind it is, and no freshness check can see it — the 5% filings
# sat at fourteen weeks with 74,000 filings in the bucket. With this set, each
# nightly run also reads this many archive days below its own floor until it
# reaches the bottom, and a cold start takes one slice instead of the whole
# archive with the port closed. Forty days is about ten minutes of the refresh
# window for every extractor together, and reaches the floor of a five-year
# archive in roughly a month. Set to 0 to go back to forward-only.
ENV EQUITY_CATCH_UP_DAYS=40

CMD ["./start.sh"]
