FROM debian:bullseye-slim

# Older OpenSSL (1.1.1) than modern host OS packages, which still verifies
# DSA host keys with non-standard parameter sizes and negotiates legacy
# KEX/cipher/MAC algorithms (diffie-hellman-group1-sha1, ssh-dss,
# aes128-cbc, hmac-sha1/md5) that newer OpenSSH builds have dropped
# entirely. Used only for devices dnsmasq-ui otherwise can't reach at all
# (e.g. old switches) — see README.md's Dynamic DNS Tracking section.
RUN apt-get update \
    && apt-get install -y --no-install-recommends openssh-client expect \
    && rm -rf /var/lib/apt/lists/*

ENTRYPOINT ["expect", "-f", "-"]
