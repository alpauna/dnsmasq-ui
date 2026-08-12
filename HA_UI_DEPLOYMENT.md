# HA UI Deployment — Superseded

This document described a Docker + GlusterFS approach to HA UI deployment
that was never actually built — only a single node (`192.168.0.233`) ran
the dashboard in practice, and the VIP (`192.168.0.250`) it references was
never the real one (`192.168.0.230`).

The dashboard's actual HA deployment is much simpler: a bare systemd
service + venv on all three DNS servers, fronted by the existing keepalived
VIP, with state sync and poller gating handled inside the app itself. It's
implemented, deployed, and verified against production.

See the **[High Availability UI Deployment](README.md#high-availability-ui-deployment)**
section of the README for the real setup, verification, and failover-testing
steps, and `ansible/dnsmasq-ui-ha.yml` for the playbook.
