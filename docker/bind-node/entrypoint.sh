#!/bin/bash
set -e

# BIND9 Phase 0 sandbox entrypoint. Deliberately minimal compared to
# docker/dns-node/entrypoint.sh -- no keepalived here, this image only
# exists to validate BIND9 mechanics (named.conf/zone generation,
# named-checkconf/named-checkzone, rndc, AXFR primary/secondary,
# dnssec-policy) ahead of the real Ansible playbook. HA behavior is
# Phase 3's concern, tested against the real servers.

mkdir -p /run/sshd /root/.ssh
if [ ! -f /etc/ssh/ssh_host_rsa_key ]; then
    ssh-keygen -A
fi

if [ -n "$SSH_PUBLIC_KEYS" ]; then
    echo "$SSH_PUBLIC_KEYS" >> /root/.ssh/authorized_keys
    chmod 600 /root/.ssh/authorized_keys
fi
echo "PermitRootLogin yes" >> /etc/ssh/sshd_config

# named.conf/zone files are supplied by whoever's driving the Phase 0
# tests (mounted or scp'd in) -- this entrypoint doesn't generate them,
# to keep this image a faithful "just BIND9 + tools" target rather than
# baking in test-specific content.
mkdir -p /etc/bind/zones

/usr/sbin/sshd -D &
echo "[+] sshd started"

if [ -f /etc/bind/named.conf.local ] && grep -q "^zone" /etc/bind/named.conf.local 2>/dev/null; then
    echo "[+] Starting named..."
    exec /usr/sbin/named -g -u bind
else
    echo "[*] No zones configured yet in /etc/bind/named.conf.local -- sleeping for manual setup via docker exec"
    tail -f /dev/null
fi
