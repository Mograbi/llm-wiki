# The librarian: an unattended maintenance agent for an llm-wiki vault.
#
# Runs Claude Code in print mode against its own clone of the vault, on its own
# branch, once a day. Bullseye base on purpose: glibc 2.31 still runs on old
# kernels (Jetson L4T 4.9 + Docker 20.10 cannot start glibc >= 2.34 images).
# Build from the repository root:  docker build -f librarian/Dockerfile -t llm-wiki-librarian .
FROM python:3.11-slim-bullseye

ARG NODE_VERSION=20.20.2
ARG CLAUDE_CODE_VERSION=2.1.296

# bullseye is past LTS: its packages live on archive.debian.org and the Release files are stale
RUN printf 'deb http://archive.debian.org/debian bullseye main\ndeb http://archive.debian.org/debian-security bullseye-security main\n' > /etc/apt/sources.list \
 && rm -f /etc/apt/sources.list.d/* \
 && echo 'Acquire::Check-Valid-Until "false";' > /etc/apt/apt.conf.d/99archive \
 && apt-get update \
 && apt-get install -y --no-install-recommends git ca-certificates curl xz-utils openssh-client \
 && rm -rf /var/lib/apt/lists/*

RUN set -eux; \
    case "$(uname -m)" in aarch64) a=arm64 ;; x86_64) a=x64 ;; *) echo "unsupported arch" && exit 1 ;; esac; \
    curl -fsSL "https://nodejs.org/dist/v${NODE_VERSION}/node-v${NODE_VERSION}-linux-${a}.tar.xz" \
      | tar -xJ -C /usr/local --strip-components=1; \
    node --version; \
    npm install -g "@anthropic-ai/claude-code@${CLAUDE_CODE_VERSION}"; \
    npm cache clean --force

RUN useradd -m -u 1000 librarian
COPY agent /opt/llm-wiki/agent
COPY skill /opt/llm-wiki/skill
COPY librarian/run.sh librarian/prompt.md /opt/llm-wiki/librarian/
RUN pip install --no-cache-dir /opt/llm-wiki/agent && chmod +x /opt/llm-wiki/librarian/run.sh

USER librarian
RUN mkdir -p /home/librarian/.claude/skills /home/librarian/.config/llm-wiki /home/librarian/.ssh \
 && ln -s /opt/llm-wiki/skill/llm-wiki /home/librarian/.claude/skills/llm-wiki \
 && echo /vault > /home/librarian/.config/llm-wiki/vault
ENV WIKI_VAULT=/vault \
    WIKI_CACHE=/home/librarian/.cache/llm-wiki \
    WIKI_NO_EMBED=1
WORKDIR /vault
ENTRYPOINT ["/opt/llm-wiki/librarian/run.sh"]
