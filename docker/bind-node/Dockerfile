# Phase 0 sandbox for the BIND9 migration (see /home/apauna/.claude/plans
# for the full plan). Debian-based, not Ubuntu like docker/dns-node --
# matches the real dns01/02/03 servers' OS family. Parallel to
# docker/dns-node/ deliberately: the existing dnsmasq test cluster stays
# untouched and available for side-by-side comparison during migration
# validation.
FROM debian:bookworm-slim

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y \
    bind9 \
    bind9utils \
    bind9-dnsutils \
    openssh-server \
    curl \
    iproute2 \
    net-tools \
    iputils-ping \
    sudo \
    procps \
    && rm -rf /var/lib/apt/lists/*

RUN mkdir -p /run/sshd /root/.ssh && \
    chmod 700 /root/.ssh

COPY entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

EXPOSE 22 53/tcp 53/udp

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
