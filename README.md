# dnsmasq-ui

Web-based management dashboard for dnsmasq DNS servers with multi-zone support, keepalive monitoring, and Ansible automation.

## Features

- 🖥️ **Web Dashboard**: Manage DNS records across multiple dnsmasq servers
- 🔄 **Multi-Zone Support**: Configure separate zones (ad.alshowto.com, internal.alshowto.com, etc.)
- 📊 **Server Status**: Real-time monitoring of dnsmasq service health
- ❤️ **Keepalive Tracking**: Automatic health checks and status logging
- 🤖 **Ansible Automation**: Full deployment and configuration management
- 🐳 **Docker Support**: Easy containerized deployment on all servers
- 🔐 **SSH Key Management**: Generate, upload, and distribute SSH keys to servers
- 🔑 **Password-based SSH Auth**: Initial setup with user passwords, fallback to key auth
- 🔀 **Reverse Proxy Support**: X-Forwarded headers for deployment behind nginx/Traefik/HAProxy
- 📋 **Configuration Dashboard**: Manage SSH keys and server settings from web UI
- 🔀 **Flexible Zone View**: Toggle between card and grid layouts with smart recommendations
- 💾 **Backup & Restore**: Export/import complete DNS configuration with auto-deployment
- **🚀 HA UI Deployment**: Run dnsmasq-ui on all servers with GlusterFS shared storage for automatic failover
- **📁 GlusterFS Replication**: zones.json automatically replicated across all servers (replica-3)
- **⚡ Single VIP**: Same keepalived VIP serves both DNS (port 53) and UI (port 5000)
- **🔗 WireGuard Mesh**: Full-mesh encrypted network for secure cross-cluster DNS synchronization (v2.2+)
- **🏷️ VLAN Sub-Interfaces**: Give a DNS server a real address on another subnet (via VLAN tag) without a second NIC, provisioned live from the Config page
- **🛡️ Group Update Plans**: Pluggable, script-based HA group updates (Proxmox VE VLAN presence today) with a self-reverting commit-confirm safety net, serialized one member at a time with a hard-stop-and-lock on any failure

## Architecture

### HA Deployment (Recommended)

```
┌──────────────────────────────────────────────────┐
│  192.168.0.230 (Keepalived VIP)                  │
│  ├─ :53   → dnsmasq DNS (MASTER)                │
│  └─ :5000 → dnsmasq-ui (MASTER)                 │
└──────────────┬───────────────────────────────────┘
               │
     ┌─────────┼─────────┐
     │         │         │
  dns01     dns02     dns03
 (MASTER)  (BACKUP)  (BACKUP)
  - dnsmasq      - dnsmasq      - dnsmasq
  - keepalived   - keepalived   - keepalived
  - dnsmasq-ui   - dnsmasq-ui   - dnsmasq-ui
  (Docker)       (Docker)       (Docker)

GlusterFS replica-3 volume
  └─ /opt/dnsmasq-ui-data/zones.json
     (Replicated across all 3 servers)
```

**Key Features:**
- All three servers run dnsmasq-ui in Docker containers
- zones.json is shared via GlusterFS (replica-3 means 3 copies)
- Single keepalived VIP manages both DNS and UI failover
- If any server fails, VIP moves to next MASTER within seconds
- UI remains accessible via same VIP even if one server goes down

## Quick Start

### Docker (Recommended)

```bash
# Clone and navigate
git clone https://github.com/yourusername/dnsmasq-ui.git
cd dnsmasq-ui

# Start with Docker Compose
docker-compose up -d

# Access dashboard at http://localhost:5000
```

### Manual Installation

```bash
# Install dependencies
pip install -r requirements.txt

# Run Flask app
python app.py

# Access at http://localhost:5000
```

## Interactive Setup

The **setup.sh** script provides an interactive way to configure DNS clusters of any size with dynamic Ansible playbooks and keepalived configuration.

### Features

- ✅ **Flexible Server Configuration**: Support for 1 to unlimited DNS servers
- ✅ **Three IP Input Formats**:
  - Single IP: `192.168.0.231`
  - IP Range: `192.168.0.231-233` (auto-expands)
  - Comma-Separated: `192.168.0.231, 192.168.0.240`
- ✅ **SSH Connectivity Testing**: Verifies access to all servers before generation
- ✅ **Dynamic Keepalived Configuration**: Automatic priority assignment based on server count
- ✅ **Auto-Generates**:
  - `ansible/inventory.ini` (Ansible server definitions)
  - `ansible/dnsmasq-setup.yml` (Dynamic playbook with keepalived)
  - Updated `zones.json` (New server definitions)

### Quick Setup (HA with GlusterFS)

```bash
# 1. Run interactive setup wizard
./setup.sh

# Follow the prompts:
#   1. SSH user (default: debian)
#   2. Number of servers (e.g., 3)
#   3. Server addresses (e.g., 192.168.0.231-233)
#   4. VIP address (default: 192.168.0.252)
#   5. Confirm configuration

# 2. Deploy DNS servers and keepalived
cd ansible
ansible-playbook -i inventory.ini dnsmasq-setup.yml

# 3. Deploy HA UI with GlusterFS and Docker
ansible-playbook -i inventory.ini dnsmasq-ui-ha.yml

# 4. Verify UI is accessible on all servers
curl http://192.168.0.230:5000/api/status     # Via VIP
curl http://192.168.0.231:5000/api/status     # Direct to dns01
curl http://192.168.0.232:5000/api/status     # Direct to dns02
curl http://192.168.0.233:5000/api/status     # Direct to dns03

# 5. Access dashboard in browser
# http://192.168.0.230:5000
```

### Builder VM Setup (Testing & Development)

For testing dnsmasq-ui before production deployment, use the automated builder VM deployment:

```bash
# 1. Initialize secrets and environment
bash setup-secrets.sh
source .env

# 2. Deploy builder VM (choose one):
# Option A: Debian 13 (latest packages, recommended)
bash ansible/deploy-builder-cloud-image.sh

# Option B: Debian 12 (stable alternative)
bash ansible/deploy-builder-debian12.sh

# 3. SSH into VM and verify
ssh debian@192.168.0.253
cloud-init status  # Wait for completion
docker --version

# 4. Run Docker test cluster
cd /opt/dnsmasq-ui/docker
./build-test-cluster.sh
```

**See:** [BUILDER_QUICKSTART.md](BUILDER_QUICKSTART.md) for quick reference, [BUILDER_SETUP.md](BUILDER_SETUP.md) for complete guide

### Setup Examples

**Example 1: 3-Server Cluster (High Availability)**
```bash
$ ./setup.sh
SSH user: [debian] → (press enter)
Number of servers: [3] → (press enter)
Server addresses: 192.168.0.231-233

Result:
  ✓ dns01 (192.168.0.231): MASTER, priority 150
  ✓ dns02 (192.168.0.232): BACKUP, priority 140
  ✓ dns03 (192.168.0.233): BACKUP, priority 130
```

**Example 2: 5-Server Multi-Region Cluster**
```bash
$ ./setup.sh
SSH user: ubuntu
Number of servers: 5
Server addresses: 10.0.1.100-102, 10.0.2.100-101

Result:
  ✓ dns01-dns03 in region 1
  ✓ dns04-dns05 in region 2
  ✓ Automatic cascade failover
```

**Example 3: Single Server (Development)**
```bash
$ ./setup.sh
Number of servers: 1
Server addresses: 192.168.1.100

Result:
  ✓ dns01: MASTER (no failover)
```

### Keepalived Priority System

The setup script automatically assigns keepalived priorities:

```
Servers → Priority Assignment
1       → 150 (MASTER only)
2       → 150 (MASTER), 140 (BACKUP)
3       → 150, 140, 130
4       → 150, 140, 130, 120
5       → 150, 140, 130, 120, 110
```

If the MASTER fails, the highest-priority BACKUP automatically takes over. When MASTER recovers, it automatically resumes control (preemption).

### Additional Documentation

See [SETUP_GUIDE.md](SETUP_GUIDE.md) for comprehensive setup documentation including:
- Detailed input format examples
- Troubleshooting guide
- Advanced configuration options
- Best practices for production deployments

## High Availability UI Deployment

**As of `ansible/dnsmasq-ui-ha.yml`, this is real and running** — earlier
versions of this doc (and of `HA_UI_DEPLOYMENT.md`) described a Docker +
GlusterFS design that was never actually deployed; only `.233` ran the
dashboard at all. What's here now is simpler and matches what's actually
running in production: a bare systemd service + venv on all three DNS
servers (same as the original single-instance setup), fronted by the
*existing* keepalived VIP rather than any new infrastructure.

### What This Actually Does

- **dnsmasq-ui runs on all three DNS servers**, not just one — if whichever
  node was serving the dashboard goes down, the others are already running
  it, no manual redeploy needed
- **Reachable on the existing DNS VIP** (`192.168.0.230:5000`) — no new VIP,
  no GlusterFS, no Docker for the app itself (the `docker` *connection type*
  for legacy switches is unrelated and still per-node, see Dynamic DNS
  Tracking above)
- **State stays in sync**: `zones.json`/`auth.json`/`device-credentials.json`/
  `smtp.env` are pushed to the other two nodes automatically after every
  save (`ZoneManager._sync_peer_state()`), so a failover doesn't hand off to
  a node with a different session-signing secret, a differently-keyed vault,
  or stale zone data
- **Only the current VRRP master polls tracked devices** — `dynamic_hosts`
  polling checks `_is_local_vrrp_master()` (a local `ip addr show` check, no
  SSH) before running, so three instances don't independently hammer the
  same switches/routers with redundant login attempts every cycle
- **Deliberately does not tie the dashboard's health into DNS failover** — a
  dnsmasq-ui crash recovers via systemd's `Restart=on-failure` in place,
  without moving the VIP (and therefore DNS traffic) for a UI-only problem.
  `keepalived.conf` itself is untouched by this playbook.

### Deploying It

```bash
cd ansible
SSH_KEY=~/.ssh/id_rsa ansible-playbook -i inventory.ini dnsmasq-ui-ha.yml
```

Installs git/python3-venv/docker.io, clones/pulls the repo, sets up the
venv, copies the SSH private key (already trusted on every host this app
manages — same key, no per-node generation needed), builds the
`docker/legacy-ssh` image ahead of time, and — on a node that doesn't
already have them — seeds `auth.json`/`device-credentials.json`/`smtp.env`
from a designated primary (`dns03` by default; change `primary_host` in the
playbook if that node is ever rebuilt from scratch) so a fresh node doesn't
start with no admin password or an uninitialized vault. Re-running the
playbook is safe (idempotent) and is how you push code updates to all three
— pulling new commits or dependency changes triggers an automatic restart.

### Verifying It

```bash
# Dashboard reachable via the VIP regardless of which node is active
curl -s -o /dev/null -w '%{http_code}\n' http://192.168.0.230:5000/

# Which node currently holds the VIP
for ip in 192.168.0.231 192.168.0.232 192.168.0.233; do
  ssh debian@$ip "ip -4 -o addr show | grep -q 192.168.0.230 && echo \"$ip: MASTER\" || echo \"$ip: standby\""
done
```

### Testing Failover

The DNS-level failover (VIP moving on `dnsmasq` health) is a separate,
pre-existing mechanism — see below for how it's actually implemented. To
test that the *dashboard* correctly follows the VIP regardless:

```bash
# Stop keepalived (not dnsmasq) on whichever node currently holds the VIP —
# this simulates the node being gone, which peers detect via missed VRRP
# heartbeats independent of any process-health tracking
ssh debian@<current-master> sudo systemctl stop keepalived

# Within a few seconds, the VIP should move to the next-priority node,
# and the dashboard should still respond there
curl -s -o /dev/null -w '%{http_code}\n' http://192.168.0.230:5000/

# Restore
ssh debian@<current-master> sudo systemctl start keepalived
```

Verified end-to-end against real infrastructure: a node was taken down this
way, the VIP and dashboard both moved to the next-priority node, DNS kept
resolving throughout, and priority-based failback correctly returned the
VIP to the original node once it rejoined.

### DNS Health Tracking (keepalived `track_process`)

`dnsmasq`'s own health is tracked separately from the above, via
`vrrp_track_process` in `keepalived.conf` (generated by
`ansible/dnsmasq-setup.yml`):

```
vrrp_track_process CHECK_DNSMASQ {
  process dnsmasq
  weight 0
  quorum 1
}

vrrp_instance DNS_VIP {
  ...
  track_process {
    CHECK_DNSMASQ
  }
}
```

`weight 0` means the VRRP instance drops into FAULT state — releasing the
VIP to the next-priority node — if `dnsmasq` isn't running, rather than
just nudging priority. Verified against production: stopping `dnsmasq` on
the master logged `Quorum lost for tracked process CHECK_DNSMASQ` /
`Entering FAULT STATE`, the VIP (and DNS resolution through it) moved to
the next node within a second, and restarting `dnsmasq` correctly let the
original node reclaim master via priority.

An earlier version of this config used `track_processes` (plural), which
is not a real keepalived keyword — it silently failed to parse
(`Unknown keyword 'track_processes'` in `journalctl -u keepalived`), so
`dnsmasq` crashing never actually triggered failover, only full
node/keepalived-process loss did. Fixed in both the live config on all
three servers and the `dnsmasq-setup.yml` template that generates it.

### IPv6 VIP

DNS and the dashboard are also reachable over IPv6, on
`2605:4a80:b004:b120::230` — a static address inside the LAN's currently
delegated `/64`, not a ULA. That's a deliberate tradeoff: it's simpler and
matches the v4 VIP's addressing style, but if the ISP ever rotates the
delegated prefix, this address goes stale until updated (the same class of
drift as the project's still-open DHCPv6/RA tracking work). Worth
revisiting as a ULA (`fd00::/8`) if that ever becomes a real problem — a
ULA would be immune to WAN prefix changes since it's never routed off the
LAN anyway.

A single keepalived `vrrp_instance` can't carry both an IPv4 and IPv6
`virtual_ipaddress` — VRRPv2 (IPv4 adverts) and VRRPv3 (IPv6 adverts) are
different wire protocols. IPv6 gets its own instance with its own
`virtual_router_id`, kept in lockstep with `DNS_VIP` via a
`vrrp_sync_group` so both VIPs always move together, including on a
`dnsmasq` crash detected through `DNS_VIP`'s `track_process` (sync groups
force every member instance into the same state regardless of which one's
tracking triggered it):

```
vrrp_instance DNS_VIP6 {
  ...
  virtual_router_id 52
  virtual_ipaddress {
    fe80::230/64
    2605:4a80:b004:b120::230/64
  }
}

vrrp_sync_group DNS_HA {
  group {
    DNS_VIP
    DNS_VIP6
  }
}
```

The `fe80::230` entry is a synthetic link-local listed first — VRRPv3 IPv6
adverts should be sourced from a link-local address, and without one
explicitly configured, keepalived falls back to the interface's real
link-local and just logs a warning on every start.

The dashboard itself binds `::` rather than `0.0.0.0` (relying on
`net.ipv6.bindv6only=0`, the Linux default — confirmed on all three
servers), so a single socket serves both address families rather than
needing separate v4/v6 listeners. `dnsmasq` needed no changes — it already
listens on all local addresses.

A hostname for the VIP itself is also published: `dns.ad.alshowto.com`
resolves to `192.168.0.230` (A) and `2605:4a80:b004:b120::230` (AAAA), so
scripts/clients don't need to hardcode the raw VIP addresses. The
individual nodes' own real addresses are published too, for anything that
specifically needs to reach one server rather than whichever is active —
`dns01`/`dns02`/`dns03`.ad.alshowto.com each have an AAAA record for that
server's actual RA/SLAAC address (separate from the VIP).

#### Monitoring

`/api/status` reports the IPv6 VIP the same way it already reports the v4
one — both a top-level `vip6` (the configured address) and, per server, a
`keepalived.vip6_active` boolean showing whether *that* node currently has
it assigned. In the dashboard this shows up as an "IPv6 VIP" line next to
the existing VIP display. Checked independently of the v4 `status` field
(`MASTER`/`STANDBY`) even though a `vrrp_sync_group` should always keep
them in lockstep — so a divergence (e.g. a bad `keepalived.conf` edit on
one node) shows up in monitoring instead of being silently assumed away.

Since the IPv6 VIP's address is manually managed rather than
auto-generated, it can only go stale one way: the ISP renumbers the
delegated prefix out from under it (the drift risk called out above). The
background poller — already gated to run only on whichever node currently
holds the VIP, same as the `dynamic_hosts` polling — checks this every
cycle: it reads the active node's own real RA/SLAAC `/64` (filtering
specifically for the `dynamic` flag in `ip -6 addr show`, since the VIP
address itself also shows up as `scope global` on the same interface and
would otherwise be compared against itself) and compares it to the
configured `keepalive_vip6`. A mismatch logs an error and — same
notification-only pattern as the vault-locked email, not an
auto-remediation — emails whatever address is configured for email 2FA,
with a reminder to update `keepalive_vip6` in `zones.json` and
`virtual_ipaddress` in `keepalived.conf` on all three servers by hand.

## Configuration

### zones.json

The main configuration file that defines zones, servers, and global settings:

```json
{
  "zones": [
    {
      "name": "ad.alshowto.com",
      "description": "Active Directory domain",
      "type": "local",
      "records": [
        {
          "domain": "example.ad.alshowto.com",
          "type": "A",
          "value": "192.168.0.100"
        },
        {
          "domain": "www.ad.alshowto.com",
          "type": "CNAME",
          "value": "example.ad.alshowto.com"
        }
      ]
    },
    {
      "name": "internal.alshowto.com",
      "description": "Internal services",
      "type": "local",
      "records": []
    }
  ],
  "servers": {
    "dns01": {
      "ip": "192.168.0.231",
      "hostname": "dns01",
      "port": 22,
      "enabled": true
    },
    "dns02": {
      "ip": "192.168.0.232",
      "hostname": "dns02",
      "port": 22,
      "enabled": true
    },
    "dns03": {
      "ip": "192.168.0.233",
      "hostname": "dns03",
      "port": 22,
      "enabled": true
    }
  },
  "global": {
    "upstream_dns": ["1.1.1.1", "8.8.8.8"],
    "keepalive_vip": "192.168.0.230",
    "keepalive_interval": 300
  }
}
```

**Key Sections:**
- **zones**: Array of DNS zones with records
- **servers**: Dictionary of DNS servers to manage
- **global**: Global settings (upstream DNS, VIP, health check interval)
- **dynamic_hosts**: Hosts with dynamically-assigned addresses to keep in sync (see below)

### Dynamic DNS Tracking (dynamic_hosts)

Some hosts get their address from DHCPv6/SLAAC (e.g. via a router like
opnsense) instead of a static assignment, so a record set once in `zones.json`
goes stale whenever the lease renews with a new address. `dynamic_hosts`
lets you opt specific records into automatic tracking instead of applying
that behavior to every record:

```json
"dynamic_hosts": [
  {
    "domain": "middle-01.ad.alshowto.com",
    "zone": "ad.alshowto.com",
    "record_type": "AAAA",
    "target_host": "192.168.0.250",
    "interface": "eth0",
    "ssh_user": null,
    "enabled": true,
    "last_checked": null,
    "last_value": null,
    "last_updated": null
  }
]
```

- **target_host**: IP/hostname dnsmasq-ui SSHes into to read the host's own
  current address (needs a static IP, or at least one stable way to reach it)
- **interface**: network interface on `target_host` to read the address from
- **record_type**: `AAAA` or `A` — the field being kept in sync
- A background job (interval set by `DYNAMIC_POLL_INTERVAL`, default 300s)
  checks every enabled entry, and if the live address differs from the
  stored record, updates `zones.json` and redeploys to all DNS servers
  automatically. `last_checked`/`last_value`/`last_updated` are written back
  after each check.
- Manage tracked hosts from the **Configuration** page in the dashboard, or
  via the [API](#dynamic-dns-tracking-1) directly.

#### Subnet-based tracking (poll the prefix once, not every device)

Polling every device directly, every cycle, doesn't scale well and is
exactly what made some of this project's early dynamic_hosts work flaky —
switches with awkward CLI sessions failing intermittently, extra load for
no reason. Most tracked devices turn out to share a shortcut: standard
SLAAC (RFC 4862) derives a device's 64-bit host suffix from its MAC
address via EUI-64 — insert `ff:fe` at the midpoint, flip the
universal/local bit — so if a device's suffix is MAC-derived (true for
every device tracked in this project's `ad.alshowto.com` zone: `middle-01`,
both switches, and `dns01`/`dns02`/`dns03` themselves), only the *prefix*
half of its address can actually drift (e.g. an ISP renumbering the
delegated block) — the suffix never changes on its own.

That splits tracking into two cheaper pieces. A named `subnets` registry
in `zones.json`'s global config holds each L3 segment's CIDR and a
`primary_dns` server to poll for the segment's current live IPv6 prefix:

```json
"subnets": {
  "lan": {
    "cidr_v4": "192.168.0.0/23",
    "prefix_v6": "2605:4a80:b004:b120::/64",
    "primary_dns": "dns01",
    "interface": "eth0"
  },
  "mgmt": {
    "cidr_v4": "192.168.7.0/24",
    "prefix_v6": "2605:4a80:b009:c100::/64",
    "primary_dns": "dns01",
    "interface": "eth0.7"
  }
}
```

`cidr_v4` is explicit rather than inferred from a bare last octet — this
LAN is actually a `/23` (see the keepalived VIP's mask), not the `/24` a
naive per-octet assumption would guess. `primary_dns` names a server from
`servers` (resolved to its IP at poll time, so it stays correct if that
server's IP is ever edited in one place) rather than a hand-typed
address. `interface` is which NIC on `primary_dns` actually sits on this
subnet — defaults to `eth0`, but a server that's only on a subnet via a
[VLAN sub-interface](#vlan-sub-interfaces) (like `mgmt` below) needs the
real one named here, or `poll_subnets()` would read the wrong subnet's
prefix.

Individual `dynamic_hosts` entries for devices in a subnet with a
`primary_dns` become just a MAC address instead of connection details:

```json
{
  "domain": "dns01.ad.alshowto.com",
  "zone": "ad.alshowto.com",
  "record_type": "AAAA",
  "subnet": "lan",
  "mac_address": "bc:24:11:0b:78:55"
}
```

Each poll cycle, `poll_subnets()` runs first: one SSH call per subnet (to
`primary_dns`, not the tracked device) reading
`ip -6 -o addr show eth0 scope global dynamic` — filtered for the
`dynamic` flag for the same reason the [IPv6 VIP drift check](#monitoring)
filters for it, since the keepalived VIP itself shows up as `scope global`
on the same interface and would otherwise get picked up as "the prefix."
Every subnet-tracked entry then computes its address as
`prefix | EUI64(mac_address)` — pure arithmetic, no connection to the
device at all. IPv4 has no equivalent derivation (there's nothing in an
IPv4 address that's calculable the way EUI-64 is), so an IPv4-tracked
entry instead stores an explicit `ipv4_host` number, combined with the
subnet's `cidr_v4`.

#### Devices with a manually-assigned address (ipv6_host)

Not every device on a tracked subnet is MAC-derived. A Proxmox host given
a hand-configured static address on the `mgmt` VLAN, for example, doesn't
self-configure via SLAAC, so there's no MAC to derive a suffix from —
`mac_address` doesn't apply. `ipv6_host` covers this case instead: an
explicit, manually-chosen 64-bit suffix combined with the subnet's live
`prefix_v6`, the same addressing style already used for the [IPv6
VIP](#ipv6-vip) itself (`2605:4a80:b004:b120::230` — prefix plus a chosen
`::230`):

```json
{
  "domain": "pve01.mgmt.alshowto.com",
  "zone": "mgmt.alshowto.com",
  "record_type": "AAAA",
  "subnet": "mgmt",
  "ipv6_host": "::11"
}
```

`prefix | ipv6_host` → `2605:4a80:b009:c100::11`. A subnet-tracked AAAA
entry needs exactly one of `mac_address` or `ipv6_host` — not both.

The catch: on its own, this only keeps the *DNS record* in sync when
`poll_subnets()` detects the delegated prefix has changed. Unlike a
MAC-derived address, the device's own static interface configuration
doesn't auto-follow the prefix — if the ISP/router ever rotates it,
`pve01`'s actual interface still needs to be updated separately, or DNS
and the device's real address go out of sync. This is the same class of
manual follow-up as the [IPv6 VIP drift monitoring](#monitoring) already
requires — unless `proxmox_update` closes that gap too (below).

#### Closing the loop: auto-pushing to a Proxmox VE node (proxmox_update)

For a Proxmox VE hypervisor specifically, the manual step above can be
automated. Add `proxmox_update` alongside `ipv6_host`:

```json
{
  "domain": "pve01.mgmt.alshowto.com",
  "zone": "mgmt.alshowto.com",
  "record_type": "AAAA",
  "subnet": "mgmt",
  "ipv6_host": "::11",
  "proxmox_update": { "node": "pve01", "iface": "vlan7" }
}
```

When `poll_subnets()` detects the subnet's prefix has actually *changed*
(not just been read for the first time), every entry on that subnet with
both `ipv6_host` and `proxmox_update` set gets its recomputed address
pushed straight to the named Proxmox node's own interface via
[`pvesh`](https://pve.proxmox.com/pve-docs/pvesh.1.html) over SSH (as
`PROXMOX_SSH_USER`, default `root` — the API token in `.env` only has
read access, `Sys.Audit`, not `Sys.Modify`, confirmed directly against a
real node). `node` must be a name from `PROXMOX_NODES`/`PROXMOX_IPS`
(semicolon-separated, matching the existing `.env` convention), and
`iface` must be a `type=vlan` interface on it — bond/bridge/OVS
interfaces aren't supported, since their field sets haven't been
validated.

**This turned out to have a real footgun worth knowing about if you're
extending it**: Proxmox's network API is a full replace, not a merge — a
request containing only the IPv6 fields silently drops the interface's
entire IPv4 configuration from the pending change (confirmed directly:
`families` went from `["inet","inet6"]` down to `["inet6"]`, IPv4
address/gateway/method just gone). So every push re-fetches the
interface's complete current config first and resends every field
unchanged except the new `cidr6`, rather than a scoped update
(`cidr`/`cidr6` combined-form fields are used throughout rather than
separate address+netmask pairs — `pvesh`'s `--netmask` wants dotted-quad
notation while `GET` returns a bare prefix length, another mismatch
worth avoiding). After staging, the pending interface config is
re-fetched and diffed against what was expected; if anything besides
`cidr6`/`address6` differs, the whole pending change is reverted
(`pvesh delete /nodes/<node>/network`, which discards it) instead of
being applied — a bad automated push should fail loud and leave the
hypervisor's networking exactly as it was, not half-changed.

An email notification is sent either way (success or failure) to the
address configured for email 2FA, same as the IPv6 VIP drift notice —
even a successful unattended change to hypervisor networking is worth
knowing about.

#### Commit-confirm: surviving a push that breaks reachability

The diff-check above only proves the *config being staged* looks
correct — it can't catch a case where an otherwise-valid config still
breaks the actual network path (e.g. the new prefix isn't really routed
yet). If that happens after applying, a plain client-side revert is
useless: the same connection needed to send it may be the thing that
just broke. `commit_proxmox_interface_v6()` (what `poll_subnets()`
actually calls, layered on top of `update_proxmox_interface_v6()`)
closes that gap with a commit-confirm handshake, the same pattern
network gear uses for exactly this risk:

1. **Before touching anything**, schedule an unconditional self-revert
   on the node's *own* systemd — `systemd-run --on-active=<timeout>
   --unit=dnsmasq-ui-revert-<iface> /bin/bash -c '<rebuild old config> &&
   <apply>'` (`at` would be the traditional tool for this, but isn't
   installed on these nodes and `systemd-run` needs nothing extra —
   confirmed directly, including that cancelling via `systemctl stop
   <unit>.timer` reliably removes it). Because this runs locally on the
   node, it fires even if dnsmasq-ui loses all contact with the node
   right after applying — the one failure mode a client-side-only revert
   can't cover.
2. Stage, diff-verify, and apply the real change (`update_proxmox_interface_v6`).
3. **Independently validate** the node is actually reachable at the new
   address: `ping6` to the new address itself, plus a fresh SSH connect
   to the node's stable mgmt IP (proves the box as a whole is still up,
   not just that one address answers ICMP).
4. Only if validation passes: cancel the revert timer. Otherwise — including
   if dnsmasq-ui can't even reach the node to check — do nothing and let
   the timer fire on its own; the node self-heals with no further action
   needed.

`PROXMOX_COMMIT_TIMEOUT_SECONDS` (default `300`) controls the window.

#### Group Update Plans: one node at a time, hard stop, per-group lockout

`proxmox_update` entries don't get pushed individually — they're
processed as members of a declared **Group Update Plan**: a named set
of HA members that must *all* converge to a target state before the
group counts as updated, not just each individually attempted. This is
a level above the commit-confirm handshake itself (which only guarantees
one member's push is safe) — it's the framework that decides *which*
members get touched, in what order, and what happens when one of them
fails.

```json
"update_groups": {
  "mgmt-vlan-proxmox": {
    "description": "Proxmox VE nodes with static IPv6 presence on the mgmt VLAN",
    "script": "proxmox_vlan_commit",
    "members": ["pve01.mgmt.alshowto.com"],
    "lock": { "locked": false, "reason": null, "member": null, "since": null }
  }
}
```

- **members**: `dynamic_hosts` domains (reusing that entry's `subnet`/
  `ipv6_host`/`proxmox_update` fields — no duplication)
- **script**: which pluggable commit/verify implementation this group
  uses (see below) — different *kinds* of HA members need completely
  different mechanics, so the framework doesn't hardcode how any
  particular one gets updated

When a subnet's prefix changes, every affected group's members are
processed **strictly one at a time, in declared order** — each must
fully commit-confirm before the next is even attempted. The first
failure halts that group immediately: every remaining member in it is
left completely untouched, not skipped-and-continued, and the group's
own `lock` engages — blocking further updates *for that group only*
(other groups, and DNS-side `ipv6_host` tracking generally, keep working
normally). A detailed failure email goes out — which member failed, why,
whether it self-reverted via the timer or was never applied, and which
other members got skipped as a result.

Unlocking isn't a plain toggle. The Configuration page's "Verify &
Unlock" button on a locked group (`POST /api/update-groups/<name>/unlock`)
re-checks every member's *live* state first — recomputes the expected
target from current live conditions, confirms the member actually
matches it, and re-runs the same reachability check as the commit-confirm
handshake — and only clears the lock if every single member checks out.
A partial pass leaves it locked and reports exactly which member(s) are
still wrong, since an admin clicking "it's fine now" isn't the same as
it actually being fine.

**Scripts** are a registry of Python method pairs (`commit(entry,
target_state) -> (bool, str)`, `verify(entry) -> (bool, str)`) —
deliberately code, not something definable from `zones.json` or the
Config page, since a script is real vendor-specific logic (SSH/API
calls, quirks), not safe to hand an admin form. `proxmox_vlan_commit`
(wrapping `commit_proxmox_interface_v6`) is the first one. The
framework is written so a completely different kind of HA member — the
still-on-hold opnsense/pfsense DHCPv6/RA drift fix, once that's actually
researched — can register its own script and reuse the exact same
group/lock/toll-gate machinery without touching any of this code, just
adding a new entry to the script registry and a new group in
`zones.json`.

Add/remove group membership from the Config page, or directly:

```bash
curl -X POST http://localhost:5000/api/update-groups/mgmt-vlan-proxmox/members \
  -H "Content-Type: application/json" -d '{"domain": "pve02.mgmt.alshowto.com"}'

curl -X DELETE http://localhost:5000/api/update-groups/mgmt-vlan-proxmox/members/pve02.mgmt.alshowto.com
```

Verified against production: recomputing all six LAN devices' addresses
this way matched their existing DNS records exactly, using a single SSH
call to `dns01` — no connections to either switch or to `dns02`/`dns03` at
all. A device's MAC doesn't need to be looked up by hand either — it can
be recovered by reversing EUI-64 on an already-known-good address (used to
migrate all six above), which also doubles as confirmation the device is
actually MAC-derived to begin with (a privacy/stable-random address won't
carry the `ff:fe` midpoint EUI-64 requires and this will error rather than
silently compute nonsense).

A subnet only helps once something in it can be reliably polled for the
live prefix. `mgmt` (`192.168.7.0/24`) had no DNS server on it at all
originally, so it had no `primary_dns` and the one device on it,
`wifi.mgmt.alshowto.com`, fell back to the older direct-polling approach
(`target_host` + `connection: http` — a login flow against the device's
own web UI). That approach was the flakiest tracked device in this
project's history (login flow, `detect_regex`, session-param handling all
had to hold up every cycle). Giving `dns01` a real presence on `mgmt` via
a [VLAN sub-interface](#vlan-sub-interfaces) let `mgmt` get a proper
`primary_dns` like `lan` has, so `wifi.mgmt.alshowto.com` now uses the
same subnet+MAC tracking as everything else — no HTTP requests at all.
The `connection: http` approach still exists in the code for subnets that
genuinely have nothing pollable on them.

#### Advanced: Non-Linux Devices (Switches, Routers)

The basic example above assumes a Linux host with `ip addr` — fine for
servers and VMs, but switches/routers have their own CLI and no `eth0` to
query. Three additional fields cover that, and `connection` picks how the
command actually gets run. Iterate on `detect_command`/`detect_regex`
against a real device via `POST /api/dynamic-hosts/test` (or the "Test
Detection" button in the dashboard) before saving — it returns the raw
command output and either the extracted address or why extraction failed,
so you're not guessing blind against hardware you can only reach once at a
time.

**Cisco-style switch behind an old DSA SSH host key**, validated against a
real TP-Link `TL-SX3008F-1`: paramiko rejects the key outright (non-standard
DSA modulus size `cryptography` refuses to parse), and modern OpenSSH
(9.8+) has dropped `ssh-dss` support entirely, so neither the default
connection nor `cli` can even complete the handshake. `connection: "docker"`
runs the session inside a container with an older OpenSSH client
(`docker/legacy-ssh/`, built automatically on first use) that still
negotiates the legacy algorithms. This switch's embedded SSH server also
only supports an interactive session, not one-shot `ssh host command`
execution, and has a separate privileged mode (`enable`) gating `show`
commands — both handled by driving the session through expect rather than
a plain command:

```json
{
  "domain": "10g-sw01.ad.alshowto.com",
  "zone": "ad.alshowto.com",
  "record_type": "AAAA",
  "target_host": "192.168.0.2",
  "ssh_user": "admin",
  "enabled": true,
  "connection": "docker",
  "ssh_extra_args": [
    "-o", "KexAlgorithms=+diffie-hellman-group1-sha1",
    "-o", "HostKeyAlgorithms=+ssh-dss",
    "-o", "MACs=+hmac-sha1,hmac-md5",
    "-c", "aes128-cbc"
  ],
  "enable_command": "enable",
  "cli_prompt_regex": "[>#]\\s*$",
  "logout_command": "exit",
  "detect_command": "show ipv6 interface",
  "detect_regex": "([0-9a-fA-F:]+), subnet is"
}
```

The regex specifically anchors on `, subnet is` (unique to the global
unicast address line in this switch's `show ipv6 interface` output) rather
than a generic IPv6 pattern, because the same output also contains a
link-local address (`fe80::...`) that a naive regex would match first —
worth checking the raw output via `/api/dynamic-hosts/test` for exactly
this kind of false-match risk before trusting a regex.

**A device requiring a privileged-mode password, or that doesn't accept the
SSH key at all**: `enable_password_ref`/`ssh_password_ref` reference a name
in the encrypted device-credentials vault (see below) rather than embedding
a plaintext password in `zones.json`:

```json
{
  "domain": "old-router.ad.alshowto.com",
  "zone": "ad.alshowto.com",
  "record_type": "A",
  "target_host": "192.168.0.3",
  "ssh_user": "admin",
  "enabled": true,
  "connection": "docker",
  "ssh_password_ref": "old-router-login",
  "enable_command": "enable",
  "enable_password_ref": "old-router-enable",
  "detect_command": "show ip interface brief",
  "detect_regex": "Vlan1\\s+(\\d{1,3}\\.\\d{1,3}\\.\\d{1,3}\\.\\d{1,3})"
}
```

Both `*_password_ref` values are just keys — the actual passwords are set
separately via the Configuration page's Device Credentials section (or
`PUT /api/device-credentials/<key>`), encrypted at rest, and require
unlocking the vault once per service restart before they're usable (see
Two-Factor Authentication's sibling section above for the general
unlock-once-per-restart pattern — same idea, different vault).

**A device with legacy algorithms but a modern-enough SSH server to still
support one-shot commands** (no interactive-only CLI, no DSA host key
paramiko can't parse) doesn't need the full `docker` treatment —
`connection: "cli"` shells out to the host's own `ssh` binary, which is
usually enough:

```json
{
  "domain": "old-nas.ad.alshowto.com",
  "zone": "ad.alshowto.com",
  "record_type": "A",
  "target_host": "192.168.0.4",
  "ssh_user": "admin",
  "enabled": true,
  "connection": "cli",
  "ssh_extra_args": ["-o", "HostKeyAlgorithms=+ssh-rsa"],
  "detect_command": "ip -4 -o addr show eth0 scope global | awk '{print $4}' | cut -d/ -f1"
}
```

### VLAN Sub-Interfaces

A DNS server sometimes needs a real IPv4/IPv6 presence on another subnet
it isn't otherwise on — for example so it can act as [`primary_dns`](#subnet-based-tracking-poll-the-prefix-once-not-every-device)
for that subnet's tracking. Adding another physical/virtual NIC per subnet
doesn't scale, but the Proxmox bridge these VMs already sit on is
VLAN-aware (trunk mode), so a persistent VLAN sub-interface (netplan)
gives the guest OS a tagged presence on that VLAN without touching
hardware — the guest-OS-level equivalent of adding a tagged NIC in the
Proxmox UI, just scriptable from here.

**This only handles the guest-OS side** — the underlying Proxmox
bridge/port for that VM must already be trunking the VLAN, or the
sub-interface comes up locally with no traffic actually reaching it (no
ARP replies, no router advertisements). If a newly-added VLAN interface
never picks up an address, check the trunk on the Proxmox host before
assuming the netplan config is wrong.

Manage VLANs per server from the **Configuration** page, or via the
[API](#vlan-management) directly. Each entry lives in `zones.json` under
that server's entry in `servers`:

```json
"servers": {
  "dns01": {
    "ip": "192.168.0.231",
    "hostname": "dns01",
    "port": 22,
    "enabled": true,
    "vlans": [
      {
        "vlan_id": 7,
        "name": "mgmt",
        "ipv4_mode": "static",
        "ipv4_address": "192.168.7.231/24",
        "ipv6_mode": "slaac",
        "ipv6_address": null
      }
    ]
  }
}
```

- **vlan_id**: 802.1Q VLAN tag (1-4094) — the sub-interface is named
  `eth0.<vlan_id>`
- **name**: label for the netplan file (`/etc/netplan/90-presence-<name>.yaml`)
  and for referencing the VLAN in the UI
- **ipv4_mode**/**ipv6_mode**: `none`, `dhcp` (v4 only), `static`, or
  `slaac` (v6 only) — `static` requires the matching `ipv4_address`/
  `ipv6_address` in CIDR form (e.g. `192.168.7.231/24`)

Adding or updating a VLAN writes the netplan file over SSH and runs
`netplan apply` immediately, so it takes effect right away rather than
waiting for the next Ansible run. Removing one deletes the netplan file
and tears down the live interface (`ip link delete eth0.<vlan_id>`) —
note that reliably removing an already-created VLAN link isn't guaranteed
just because its config file is gone, so verify with `ip addr` after a
delete if it matters.

### Environment Variables

```bash
# Configuration
export ZONES_CONFIG=zones.json                           # Zone and server config file
export DNSMASQ_RECORDS_FILE=/etc/dnsmasq.d/local-records.conf  # dnsmasq output path

# SSH Configuration
export SSH_KEY=~/.ssh/id_rsa                            # Private key for SSH auth
export SSH_USER=debian                                   # SSH username for servers

# WireGuard Configuration
export WG_KEYS_FILE=wireguard-keys.json                # Private keys file (gitignored)

# Dynamic DNS Tracking
export DYNAMIC_POLL_INTERVAL=300                        # Seconds between dynamic_hosts checks
export DASHBOARD_URL=http://192.168.0.233:5000          # Used in links for emails sent from background contexts

# Proxmox VE auto-update (see proxmox_update, VLAN Sub-Interfaces)
export PROXMOX_SSH_USER=root                            # SSH user for pvesh commands, not the DNS-server SSH_USER
export PROXMOX_NODES='pve01;pve04;pve06;pve3'           # ';'-separated node names, paired positionally with PROXMOX_IPS
export PROXMOX_IPS='192.168.7.11;192.168.7.14;192.168.7.16;192.168.7.13'
export PROXMOX_COMMIT_TIMEOUT_SECONDS=300               # Self-revert window for the commit-confirm handshake

# Reverse Proxy Support
export PROXY_PATH_PREFIX=/dnsmasq-ui                    # URL path prefix (optional)
export TRUSTED_PROXIES=*                                # Trusted proxy IPs (or '*' for all)
```

### WireGuard Mesh Network

Enable encrypted full-mesh networking between DNS servers for secure cross-cluster communication and automatic DNS synchronization in disconnected networks.

#### Configuration in zones.json

Per-server WireGuard configuration (public keys only):
```json
{
  "servers": {
    "dns01": {
      "ip": "192.168.0.231",
      "wireguard": {
        "public_key": "BASE64-ENCODED-PUBLIC-KEY",
        "tunnel_ip": "10.99.0.1/24",
        "listen_port": 51820,
        "generated": "2026-03-15T00:00:00"
      }
    }
  },
  "global": {
    "wireguard": {
      "enabled": false,
      "mesh_subnet": "10.99.0.0/24",
      "listen_port": 51820,
      "persistent_keepalive": 25
    }
  }
}
```

**Key Security Points:**
- Private keys stored in gitignored `wireguard-keys.json` (0600 permissions, never in version control)
- Public keys distributed via zones.json (safe to commit)
- Enable WireGuard by setting `global.wireguard.enabled: true`
- Each node automatically gets a tunnel IP (10.99.0.1, 10.99.0.2, etc.)

#### Workflow

```bash
# 1. Generate WireGuard keypairs for all servers
curl -X POST http://localhost:5000/api/wireguard/generate-keys

# 2. Validate configuration
curl http://localhost:5000/api/wireguard/validate

# 3. Preview WireGuard config for a server
curl http://localhost:5000/api/wireguard/config/dns01

# 4. Deploy mesh to all servers
curl -X POST http://localhost:5000/api/wireguard/deploy

# 5. Check mesh health
curl http://localhost:5000/api/wireguard/status
```

#### What Happens After Deployment

- Each node installs `wireguard-tools` and runs `wg-quick up wg0`
- dnsmasq listens on `wg0` interface (in addition to physical interfaces)
- Full-mesh topology: each node peers with all others
- All DNS queries can traverse encrypted tunnels
- Keepalived VIP works alongside WireGuard (separate networks)
- Health checks monitor peer connectivity and interface status

#### Use Cases

- **Disconnected Networks**: DNS servers in isolated subnets can sync via WireGuard tunnel
- **Security**: Encrypt DNS traffic between internal servers
- **Multi-Site Clusters**: Connect DNS servers across different networks or datacenters
- **VPN Integration**: Integrate with existing WireGuard infrastructure

### Reverse Proxy Configuration

For deployment behind nginx, Traefik, or HAProxy, enable X-Forwarded header support. See [REVERSE_PROXY.md](REVERSE_PROXY.md) for detailed nginx/Traefik/HAProxy examples.

**Key Headers Supported:**
- `X-Forwarded-For` - Client IP tracking
- `X-Forwarded-Proto` - HTTP vs HTTPS detection
- `X-Forwarded-Host` - Original hostname
- `X-Forwarded-Port` - Original port

**Example Nginx Configuration:**
```nginx
location /dnsmasq-ui/ {
    proxy_pass http://192.168.0.233:5000/;

    # Enable reverse proxy support
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_set_header X-Forwarded-Host $server_name;
    proxy_set_header X-Forwarded-Port $server_port;
    proxy_set_header Host $host;
}
```

## Deployment

### Option 1: Interactive Setup (Recommended)

Use the setup script to automatically generate Ansible configuration:

```bash
# Run interactive setup
./setup.sh

# Deploy with Ansible
cd ansible
ansible-playbook -i inventory.ini dnsmasq-setup.yml
```

### Option 2: Manual Ansible Deployment

If you prefer to manually configure servers:

```bash
# Install Ansible
pip install ansible

# Configure inventory
cd ansible
vim inventory.ini  # Update IPs and SSH keys

# Run playbook
ansible-playbook -i inventory.ini dnsmasq-setup.yml
```

### Option 3: Deployment Script

Use the quick deployment script without Ansible:

```bash
# Deploy to all servers
./deploy-keepalived.sh all

# Deploy to specific server
./deploy-keepalived.sh dns01

# View options
./deploy-keepalived.sh --help
```

### Playbook Features

- Installs dnsmasq on all servers
- Configures local DNS records
- Sets up keepalive health checks via cron
- Disables systemd-resolved to avoid conflicts
- Starts and enables dnsmasq service

## Monitoring & Keepalived

### Real-Time Keepalived Status
The dashboard displays keepalived status for each DNS server:

- **MASTER** (green badge): Server is the active failover master with VIP assigned
- **STANDBY** (orange badge): Keepalived is running but this is not the master
- **INACTIVE** (gray badge): Keepalived service is not running

Status updates automatically every 30 seconds via the `/api/status` endpoint.

### Legacy Health Checks
Each DNS server can run a health check every 5 minutes (if configured):

```bash
# Check local status
cat /var/run/dnsmasq-status

# View health history
tail -f /var/log/dnsmasq-monitor.log

# Manual health check
/usr/local/bin/dnsmasq-monitor.sh
```

### Keepalived Monitoring via SSH
The application monitors keepalived status on each server via SSH:

```bash
# Manual status check from UI server
ssh debian@192.168.0.231 sudo systemctl status keepalived

# Check if VIP is active (only on MASTER)
ssh debian@192.168.0.231 ip addr | grep 192.168.0.230
```

## API Reference

### Zone Management

```bash
# Get all zones
curl http://localhost:5000/api/zones

# Create new zone
curl -X POST http://localhost:5000/api/zones \
  -H "Content-Type: application/json" \
  -d '{"name": "prod.alshowto.com", "description": "Production", "type": "local"}'

# Delete zone
curl -X DELETE http://localhost:5000/api/zones/prod.alshowto.com
```

### DNS Records (by Zone)

```bash
# Get records in zone
curl http://localhost:5000/api/zones/ad.alshowto.com/records

# Add record to zone
curl -X POST http://localhost:5000/api/zones/ad.alshowto.com/records \
  -H "Content-Type: application/json" \
  -d '{"domain": "example.ad.alshowto.com", "type": "A", "value": "192.168.0.100"}'

# Delete record from zone
curl -X DELETE http://localhost:5000/api/zones/ad.alshowto.com/records/example.ad.alshowto.com/A
```

### Deployment

```bash
# Deploy configuration to all servers
curl -X POST http://localhost:5000/api/deploy

# Check server status (includes keepalived status)
curl http://localhost:5000/api/status

# Response example:
# {
#   "servers": {
#     "dns01": {
#       "ip": "192.168.0.231",
#       "online": true,
#       "dnsmasq": "active",
#       "keepalived": {
#         "running": true,
#         "status": "MASTER",  // MASTER, STANDBY, or INACTIVE
#         "vip": "192.168.0.230"
#       }
#     }
#   },
#   "vip": "192.168.0.230"
# }
```

### Dynamic DNS Tracking

```bash
# List tracked hosts
curl http://localhost:5000/api/dynamic-hosts

# Start tracking a host (record_type/interface/ssh_user/enabled are optional)
curl -X POST http://localhost:5000/api/dynamic-hosts \
  -H "Content-Type: application/json" \
  -d '{"domain": "middle-01.ad.alshowto.com", "zone": "ad.alshowto.com", "target_host": "192.168.0.250", "interface": "eth0", "record_type": "AAAA"}'

# Enable/disable or change a tracked host
curl -X PUT http://localhost:5000/api/dynamic-hosts/middle-01.ad.alshowto.com \
  -H "Content-Type: application/json" \
  -d '{"enabled": false}'

# Stop tracking a host
curl -X DELETE http://localhost:5000/api/dynamic-hosts/middle-01.ad.alshowto.com

# Force an immediate poll of all tracked hosts (also runs automatically
# every DYNAMIC_POLL_INTERVAL seconds)
curl -X POST http://localhost:5000/api/dynamic-hosts/poll
```

### VLAN Management

```bash
# List a server's configured VLAN sub-interfaces
curl http://localhost:5000/api/servers/dns01/vlans

# Create and provision a VLAN sub-interface (writes netplan + applies immediately)
curl -X POST http://localhost:5000/api/servers/dns01/vlans \
  -H "Content-Type: application/json" \
  -d '{"vlan_id": 7, "name": "mgmt", "ipv4_mode": "static", "ipv4_address": "192.168.7.231/24", "ipv6_mode": "slaac"}'

# Update and re-provision an existing VLAN
curl -X PUT http://localhost:5000/api/servers/dns01/vlans/7 \
  -H "Content-Type: application/json" \
  -d '{"ipv4_mode": "none"}'

# Tear down a VLAN sub-interface
curl -X DELETE http://localhost:5000/api/servers/dns01/vlans/7
```

See [VLAN Sub-Interfaces](#vlan-sub-interfaces) for the field reference
and the Proxmox-trunk caveat.

### Update Groups

```bash
# All declared Group Update Plans and their current status
curl http://localhost:5000/api/update-groups

# Re-verify every member of a locked group's live state and clear that
# group's lock only if all of them check out
curl -X POST http://localhost:5000/api/update-groups/mgmt-vlan-proxmox/unlock

# Add/remove a member
curl -X POST http://localhost:5000/api/update-groups/mgmt-vlan-proxmox/members \
  -H "Content-Type: application/json" -d '{"domain": "pve02.mgmt.alshowto.com"}'
curl -X DELETE http://localhost:5000/api/update-groups/mgmt-vlan-proxmox/members/pve02.mgmt.alshowto.com
```

See [Group Update Plans](#group-update-plans-one-node-at-a-time-hard-stop-per-group-lockout)
for what a group is, what engages a group's lock, and what "verify" actually checks.

### SSH Key Management

```bash
# Get current SSH key info
curl http://localhost:5000/api/config/ssh

# Get list of target servers for sync
curl http://localhost:5000/api/config/ssh/servers

# Generate new SSH key pair
curl -X POST http://localhost:5000/api/config/ssh/generate

# Upload SSH private key
curl -F "private_key=@/path/to/id_rsa" \
  http://localhost:5000/api/config/ssh/upload

# Sync public key to servers (key-based auth)
curl -X POST http://localhost:5000/api/config/ssh/sync \
  -H "Content-Type: application/json" \
  -d '{"public_key": "ssh-rsa AAAA..."}'

# Sync public key with password fallback
curl -X POST http://localhost:5000/api/config/ssh/sync \
  -H "Content-Type: application/json" \
  -d '{"public_key": "ssh-rsa AAAA...", "password": "user-password"}'
```

### Backup & Restore

```bash
# Download configuration backup as JSON
curl http://localhost:5000/api/config/backup > backup.json

# Restore configuration from backup (no deployment)
curl -F "backup_file=@backup.json" \
  http://localhost:5000/api/config/restore

# Restore configuration and deploy to all servers
curl -F "backup_file=@backup.json" \
  http://localhost:5000/api/config/restore-and-deploy
```

### WireGuard Mesh Management

```bash
# Generate WireGuard keypairs for all servers
curl -X POST http://localhost:5000/api/wireguard/generate-keys

# Generate with key rotation (overwrite existing keys)
curl -X POST http://localhost:5000/api/wireguard/generate-keys \
  -H "Content-Type: application/json" \
  -d '{"overwrite": true}'

# Validate WireGuard configuration
curl http://localhost:5000/api/wireguard/validate

# Get WireGuard wg0.conf preview for a server
curl http://localhost:5000/api/wireguard/config/dns01

# Deploy WireGuard mesh to all enabled servers
curl -X POST http://localhost:5000/api/wireguard/deploy

# Deploy to single server
curl -X POST http://localhost:5000/api/wireguard/deploy/dns01

# Check WireGuard mesh status (peers, handshakes, IPs)
curl http://localhost:5000/api/wireguard/status

# Response example:
# {
#   "dns01": {
#     "wg0_up": true,
#     "peers_connected": 2,
#     "interface_ip": "10.99.0.1/24",
#     "error": ""
#   },
#   "dns02": {
#     "wg0_up": true,
#     "peers_connected": 2,
#     "interface_ip": "10.99.0.2/24",
#     "error": ""
#   }
# }
```

## Supported Record Types

- **A**: IPv4 address
- **AAAA**: IPv6 address
- **CNAME**: Canonical name (alias)

## Web UI

### Dashboard
- View all DNS servers and their status (online/offline indicators)
- **Keepalived Status Display**: Real-time monitoring of keepalived failover state
  - Shows MASTER (green), STANDBY (orange), or INACTIVE (gray) for each server
  - Displays keepalived VIP address and active master server
  - Auto-refreshes every 30 seconds
- Zone overview with record counts
- Quick health check overview
- Navigate to zone management and configuration
- Access configuration page for SSH key management

### Zone Management
- View all DNS records organized by zone
- Add new records to any zone
- Edit and delete existing records
- Inline record editing with save functionality
- Deploy changes across all servers with one click
- **Card/Grid View Toggle**: Switch between card and grid layouts
  - Card view: Multi-column layout (default for ≤3 zones)
  - Grid view: Full-width list layout (recommended for >3 zones)
  - Zone record preview showing first 3 records per zone
  - "+X more records" indicator for zones with many records
  - View preference saved in browser (persists across sessions)
  - Smart recommendation to switch to grid view when zones > 3

### Configuration Page
The configuration page (`/config`) provides SSH key, server, dynamic-DNS,
and VLAN management:

#### VLAN Sub-Interface Management
- **Per-Server VLAN List**: View each server's configured VLAN
  sub-interfaces (tag, name, IPv4/IPv6 mode and address)
- **Add VLAN**: Create a persistent VLAN sub-interface and provision it
  over SSH immediately (netplan + `netplan apply`), no Ansible run needed
- **Edit/Remove**: Update a VLAN's addressing or tear it down entirely
- Requires the underlying Proxmox bridge to already be trunking the VLAN
  to that VM — see [VLAN Sub-Interfaces](#vlan-sub-interfaces)

#### Update Groups
- **Per-Group Status**: every declared Group Update Plan, its members,
  and its lock state — green if running normally, red with the failure
  reason/timestamp/member if locked
- **Add/Remove Member**: add any tracked host as a group member, or
  remove one (blocked while the group is locked on that specific member
  — unlock first, don't remove your way around it)
- **Verify & Unlock**: re-checks every member's live state and only
  clears that group's lock if all of them match expectations — a
  partial pass reports exactly which member(s) don't

#### Dynamic DNS Tracking
- **Tracked Hosts**: Cards showing each tracked host's zone, record type,
  target, current value, and last-checked/last-updated times
- **Poll Now**: Trigger an immediate check instead of waiting for the
  background interval
- **Track a New Host**: Add a domain/zone/target/interface to start
  keeping a record in sync with the host's own current address
- **Enable/Disable/Remove**: Per-host controls, no need to hand-edit `zones.json`

#### SSH Key Management
- **View Current Key**: Display key fingerprint, type, size, and modification time
- **Generate New Keys**: One-click generation of 4096-bit RSA key pairs
- **Upload Keys**: Import existing SSH private keys for authentication
- **Sync to Servers**: Distribute public keys to all configured DNS servers

#### Password-Based SSH Authentication
For initial setup when servers don't have valid SSH keys:
- Enter target server credentials (username/password)
- System tries key-based auth first
- Falls back to password auth if key auth fails
- Automatically installs public key to authorized_keys on success
- Shows per-server sync status and results

#### Server Status
- Real-time connection status for all servers
- IP addresses and hostnames
- Auto-refreshes every 30 seconds

## File Structure

```
dnsmasq-ui/
├── app-multi-zone.py          # Flask application (multi-zone version)
├── app.py                      # Simple single-server version (reference)
├── requirements.txt            # Python dependencies
├── Dockerfile                  # Container configuration
├── docker-compose.yml          # Docker Compose setup
├── zones.json                  # Zone and server configuration
├── servers.json                # Legacy server configuration
├── CLAUDE.md                   # Development guide
├── REVERSE_PROXY.md            # Reverse proxy setup guide
├── README.md                   # This file
├── templates/
│   ├── dashboard-v2.html       # Multi-zone dashboard
│   ├── zone.html               # Zone detail and record management
│   ├── config.html             # Configuration and SSH key management
│   ├── dashboard.html          # Simple dashboard (legacy)
│   └── server.html             # Simple server management (legacy)
└── ansible/
    ├── dnsmasq-setup.yml       # Ansible playbook for server setup
    └── inventory.ini           # Ansible inventory with server definitions
```

## DNS Record Format

Records are stored in dnsmasq format in `/etc/dnsmasq.d/local-records.conf`:

```
# A records
address=/example.ad.alshowto.com/192.168.0.100

# AAAA records (IPv6)
address=/example.ad.alshowto.com/2604:7a00:ea40::100

# CNAME records
cname=www.ad.alshowto.com,example.ad.alshowto.com

# Upstream DNS
server=1.1.1.1
server=8.8.8.8
```

## Security Considerations

- **SSH Keys**: Primary authentication method is SSH key-based (secure by default)
- **Password Authentication**: Optional fallback for initial setup when keys aren't available yet
- **Credentials Storage**: SSH keys should be stored securely at `~/.ssh/id_rsa` with 0600 permissions
- **Network**: Run dnsmasq-ui on protected network or behind firewall
- **Access Control**: The dashboard requires a login (see Dashboard Authentication below) — set this up before exposing the port beyond a trusted network
- **Reverse Proxy**: Full support for X-Forwarded headers when deployed behind nginx/Traefik/HAProxy
- **Logs**: Client IP tracking via X-Forwarded-For headers automatically logged

### Dashboard Authentication

Every route requires login except `/setup` and `/login`; API requests without
a valid session get a `401` instead of leaking data.

- **First run**: visiting the dashboard redirects to `/setup` to choose an
  admin password (min. 8 characters). There's one shared password — this
  isn't a multi-user system.
- **Login/logout**: `/login`, and a Logout link in the dashboard and
  Configuration page headers (`POST /logout`).
- **Session**: signed cookie (`HttpOnly`), survives service restarts once a
  password has been set, since the signing secret is persisted alongside the
  password hash in `auth.json`.
- **Forgot the password (or locked out of 2FA)**: there's no reset flow by
  design (single shared password, no separate account-recovery email).
  Delete `auth.json` on the server and restart `dnsmasq-ui.service` —
  this wipes the password hash *and* any TOTP/email 2FA config together
  (they live in the same file), and `/setup` runs again on the next visit.
  There's no way to reset just the password while keeping 2FA enabled, or
  vice versa.
  ```bash
  ssh debian@<server> "rm /opt/dnsmasq-ui/auth.json && sudo systemctl restart dnsmasq-ui"
  ```
- **CSRF protection**: `Flask-WTF`'s `CSRFProtect` guards every state-changing
  request app-wide. Forms carry a hidden `csrf_token` field; the dashboard's
  JS pages patch `window.fetch` once (per page, via a small snippet right
  after the `<script>` tag) to auto-attach an `X-CSRFToken` header to every
  non-GET request, so none of the individual `fetch()` calls needed
  touching. A request missing/mismatching the token gets a `400`.
- **Testing the login programmatically**: use `curl --data-urlencode`, not
  `-d`, if the password contains `&` or other reserved URL characters — `-d`
  sends the value unencoded, so the receiving form parser treats `&` as a
  field separator and silently truncates the password at that point. `/login`
  itself is CSRF-protected too, so fetch a token from the page first.
  ```bash
  # Wrong — truncates at the & if the password contains one:
  curl -c cookies.txt -d "password=$PASSWORD" http://<server>:5000/login

  # Correct: get a session + CSRF token, then log in with both
  curl -c cookies.txt http://<server>:5000/login -o login.html
  CSRF=$(grep -o 'name="csrf_token" value="[^"]*"' login.html | sed 's/.*value="//;s/"$//')
  curl -b cookies.txt -c cookies.txt \
    --data-urlencode "csrf_token=$CSRF" --data-urlencode "password=$PASSWORD" \
    http://<server>:5000/login

  # Authenticated requests: send the cookie; state-changing ones also need
  # the X-CSRFToken header (fetch a fresh token from any page's meta tag)
  curl -b cookies.txt http://<server>:5000/api/zones
  ```

### Two-Factor Authentication

Opt-in, per-method — enable either or both from the Configuration page's
Two-Factor Authentication section. Whichever are enabled are all offered at
login (`/login/verify`), so you pick whichever's convenient that time
instead of being locked into one.

- **TOTP (authenticator app)**: `POST /api/2fa/totp/setup` issues a new
  secret (shown as text + an `otpauth://` URI — no QR image, to avoid
  pulling in a `qrcode`/`Pillow` dependency chain for something an
  authenticator app's manual-entry option already covers). Enabling requires
  proving you can generate a valid code from it via
  `POST /api/2fa/totp/confirm` first — it's not live until confirmed.
- **Email**: `POST /api/2fa/email/setup` sends a 6-digit code to the given
  address via the SMTP relay configured in `smtp.env` (see below).
  `POST /api/2fa/email/confirm` with that code enables it. Codes expire
  after 10 minutes and are tracked in an in-memory dict, not the session
  cookie or disk — lost on service restart, which just means a half-finished
  setup/login has to restart, nothing more.
- **Disabling** either method requires the current dashboard password again
  (`POST /api/2fa/totp/disable` / `/api/2fa/email/disable`) — a hijacked
  session alone can't strip 2FA off the account.
- TOTP secrets are stored in `auth.json` alongside the password hash,
  protected the same way (`0600`, gitignored) — not further encrypted, since
  unlike the device-credentials vault, verifying a TOTP code has to happen
  *during* login itself, before any "vault unlock" step could exist.

### Vault-Locked Email Notification

Every service restart drops the in-memory device-credentials vault key
(see Two-Factor Authentication above for the same tradeoff applied to
TOTP/email), so password-gated `dynamic_hosts` polling silently fails
until someone happens to check. If email 2FA is configured, `dnsmasq-ui`
emails that address once per lock period when a poll finds the vault
locked and at least one enabled entry needs it
(`enable_password_ref`/`ssh_password_ref`/`login_password_ref` set).

**This is a notification, not an unlock mechanism, on purpose.** An
email-based unlock (a link or code that unlocks without the vault
password) was considered and rejected — it would mean anyone who can read
that email can unlock the vault, collapsing the two-factor separation the
vault exists for down to "however well-secured your email account is."
The email just links to `/config`; you still log in and enter the vault
password normally.

- Sent at most once per lock (resets on the next successful unlock, so a
  future lock — e.g. the next restart — sends a fresh notice)
- Recipient is whatever address email 2FA is configured for — no separate
  notification-recipient setting
- `DASHBOARD_URL` env var (default `http://192.168.0.233:5000`) controls
  the link in the email, since a background poll has no browser request
  to infer an address from

### Where Files Live on the Server

`AUTH_FILE`, `DEVICE_CREDENTIALS_FILE`, and `WG_KEYS_FILE` all default to the
same directory as `ZONES_CONFIG` (`/opt/dnsmasq-ui` in a typical deployment)
unless overridden via their respective environment variables:

| File | Path | Purpose |
|---|---|---|
| Zone/server config | `/opt/dnsmasq-ui/zones.json` | tracked in git |
| Dashboard login + TOTP secret | `/opt/dnsmasq-ui/auth.json` | `0600`, gitignored |
| Device-credential vault | `/opt/dnsmasq-ui/device-credentials.json` | `0600`, gitignored, encrypted at rest |
| WireGuard keys | `/opt/dnsmasq-ui/wireguard-keys.json` | `0600`, gitignored |
| SMTP relay credentials (email 2FA) | `/opt/dnsmasq-ui/smtp.env` | `0600`, gitignored, loaded via systemd `EnvironmentFile=` — **not** read from the unit file itself, which is world-readable (`644`) by default |
| Proxmox VE node list ([`proxmox_update`](#closing-the-loop-auto-pushing-to-a-proxmox-ve-node-proxmox_update)) | `/opt/dnsmasq-ui/proxmox.env` | Same pattern as `smtp.env` — `0600`, gitignored, `EnvironmentFile=`, peer-synced |
| SSH private key | `~/.ssh/id_rsa` (e.g. `/home/debian/.ssh/id_rsa`) | outside the app directory entirely |
| Deployed dnsmasq config | `/etc/dnsmasq.d/local-records.conf` | on each DNS server, not the dashboard host |

None of the `0600` files above are readable by `git pull`/`push` — they're
gitignored and stay local to whichever server the dashboard runs on.

`smtp.env` format (plain `KEY=value`, no quoting needed for simple values):
```
SMTP_SERVER=mail.example.com
SMTP_PORT=587
SMTP_USER=admin@example.com
SMTP_PASSWORD=your-smtp-password
SMTP_FROM=admin@example.com
```
The systemd unit references it via `EnvironmentFile=-/opt/dnsmasq-ui/smtp.env`
(the leading `-` makes it optional — the app starts fine without email 2FA
configured, that feature just won't work until the file exists).

**Troubleshooting `535 5.7.8 authentication failed`**: this is an
auth-layer rejection, not TLS/connectivity — `smtplib` calls `starttls()`
before `login()`, so getting a clean SMTP error response back means the
connection and encryption already succeeded and only the credentials were
rejected. The most common cause: `SMTP_USER` needs to be the **full email
address** (`admin@example.com`), not just the mailbox's local part
(`admin`) — many mail servers require the full address for SASL auth even
though the account is technically just "admin". Update `smtp.env` and
`sudo systemctl restart dnsmasq-ui` to pick up the change.

## Initial Setup Workflow

For first-time deployment to servers without SSH keys:

1. **Access Configuration Page**: Navigate to `http://hostname:5000/config`

2. **Generate SSH Key**:
   - Go to "Generate New" tab
   - Click "Generate New Key"
   - Copy both private and public keys
   - Save private key securely

3. **Distribute Public Key with Password Auth**:
   - Select "Sync to Servers" tab
   - Paste the public key content
   - Enter target server password (debian user password or similar)
   - Click "Sync Public Key to Servers"
   - System will:
     - Try SSH key auth first
     - If key auth fails, use password auth as fallback
     - Install public key to authorized_keys on all servers

4. **Future Operations**:
   - Use key-based authentication (no password needed)
   - System automatically uses keys for all SSH operations

This workflow ensures secure initial setup even when starting from password-only SSH access.

## Backup & Restore

The application provides built-in backup and restore functionality for complete configuration management.

### Backup Configuration

Download your complete DNS configuration (zones, records, servers) as a JSON file:

```bash
# Via Web UI: Configuration page → Backup & Restore → Backup Config → Download

# Via API:
curl http://localhost:5000/api/config/backup > dns-backup.json
```

**Backup Includes:**
- All zones and their DNS records
- Server definitions
- Global settings (upstream DNS, VIP, intervals)
- Backup timestamp and version information

### Restore Configuration

Two restore modes are available:

**1. Restore Config Only** - Update configuration without deploying:
```bash
# Via Web UI: Configuration → Restore Config → Select file → Restore Configuration

# Via API:
curl -F "backup_file=@dns-backup.json" http://localhost:5000/api/config/restore
```

**2. Restore & Deploy** - Restore and automatically push to all servers:
```bash
# Via Web UI: Configuration → Restore Config → Select "Restore & Deploy" → Restore Configuration

# Via API:
curl -F "backup_file=@dns-backup.json" http://localhost:5000/api/config/restore-and-deploy
```

When using **Restore & Deploy**, the system will:
1. Validate the backup file
2. Restore configuration to dnsmasq-ui
3. Generate dnsmasq format config
4. Deploy to all DNS servers (dns01, dns02, dns03)
5. Restart dnsmasq service on each server
6. Show per-server deployment status

### Use Cases

- **Disaster Recovery**: Restore configuration if accidentally deleted
- **Configuration Transfer**: Move DNS config between dnsmasq-ui instances
- **Version Control**: Save backups before making major changes
- **Migration**: Copy configuration from old DNS system to new instance
- **Testing**: Backup production, test changes, restore if needed

### Backup Format

Backups are standard JSON files with the following structure:

```json
{
  "backup_timestamp": "2026-03-15T00:22:58.710628",
  "version": "2.0",
  "zones": [
    {
      "name": "example.com",
      "description": "Example zone",
      "type": "local",
      "records": [...]
    }
  ],
  "servers": {
    "dns01": {
      "ip": "192.168.0.231",
      "hostname": "dns01",
      "port": 22,
      "enabled": true
    }
  },
  "global": {
    "upstream_dns": ["1.1.1.1", "8.8.8.8"],
    "keepalive_vip": "192.168.0.230",
    "keepalive_interval": 300
  }
}
```

This makes backups compatible with version control systems (git) and easy to edit manually if needed.

## Zone View Modes

The dashboard supports flexible viewing of DNS zones to accommodate varying numbers of zones.

### Card View (Default for ≤3 zones)
- **Multi-column** card layout
- **Best for**: Small number of zones (1-3)
- **Features**:
  - Compact display of zone information
  - Record preview showing first 3 records
  - Zone type badge
  - Quick access to manage/delete buttons

### Grid View (Recommended for >3 zones)
- **Full-width** list layout
- **Best for**: Many zones (4+)
- **Features**:
  - Better vertical organization
  - Scrollable interface
  - More readable on smaller screens
  - All zone info visible at once

### Smart Features

**Auto-Recommendation:**
- When zones > 3, dashboard recommends grid view
- Shows tip notification with quick switch button
- You can dismiss and use preferred view

**View Persistence:**
- Selected view mode is saved in browser
- Preference persists across sessions
- Toggle buttons at top-right of zones section
- Both views show identical zone information

**Record Preview:**
- First 3 records displayed inline
- Record type shown with colored badge (A, AAAA, CNAME)
- "+X more records" indicator for zones with 4+ records
- No need to click through to see zone contents

### Switching Views

Toggle buttons are located at the top-right of the "DNS Zones" section:
```
View: [📦 Card] [📋 Grid]
```

Click to switch instantly between views. Your preference is automatically saved!

## Comprehensive Testing

A complete test suite is available in the `tests/` directory to validate your DNS cluster deployment.

### DNS Stress Testing

Test DNS performance and reliability under load:

```bash
cd tests

# Default stress test (100 queries, 4 domains)
./dns-stress-test.sh

# High-load stress test (500 queries)
./dns-stress-test.sh --queries 500

# Test specific domain
./dns-stress-test.sh --domain dns01.ad.alshowto.com

# Show help
./dns-stress-test.sh --help
```

**Expected Results:**
- Success rate: 99%+ (excellent performance)
- No timeouts or failures
- All domains responding correctly

### Keepalived Failover Testing

Test automatic failover when master fails:

```bash
cd tests

# Run complete failover test
./run-all-tests.sh --failover

# This will:
# 1. Verify dns01 is MASTER with VIP
# 2. Stop keepalived on dns01 (simulate failure)
# 3. Confirm dns02 becomes MASTER
# 4. Verify VIP moved to dns02
# 5. Restart keepalived on dns01
# 6. Confirm dns01 resumes MASTER role
# 7. Verify DNS service continuity throughout
```

### Complete Test Suite

Run all tests together:

```bash
cd tests
./run-all-tests.sh

# This runs:
# - SSH connectivity checks
# - DNS stress test (100 queries)
# - Keepalived failover test
# - Final cluster status report
```

### Testing Documentation

See [tests/README.md](tests/README.md) for detailed testing documentation including:
- Individual test descriptions
- Usage examples for different scenarios
- Expected results and pass criteria
- Troubleshooting guide for test failures
- Performance benchmarks

## Troubleshooting

### DNS not resolving

```bash
# Check dnsmasq status
ssh debian@192.168.0.231 sudo systemctl status dnsmasq

# Test DNS directly
ssh debian@192.168.0.231 dig @127.0.0.1 example.ad.alshowto.com

# Check logs
ssh debian@192.168.0.231 sudo tail -f /var/log/dnsmasq/dnsmasq.log
```

### Deploy succeeds but the DNS answer doesn't change

If `POST /api/deploy` (or the Deploy button) reports success but `dig`/`getent`
still return the old value for a record you just edited, check these in order:

1. **`zones.json`'s `servers` section points at the wrong hosts.** It must list
   the real DNS server IPs (e.g. `192.168.0.231-233`), not the Docker
   dns-node test cluster's bridge-network IPs (`172.20.0.x` from
   `docker-compose.yml`). Deploy will SSH into whatever's listed there —
   if that's the Docker cluster, it updates a test environment nobody
   queries while production keeps serving stale records.
   ```bash
   python3 -c "import json; print(json.load(open('zones.json'))['servers'])"
   ```

2. **dnsmasq was only sent `SIGHUP`, not restarted.** `SIGHUP` reloads
   `/etc/hosts`-style dynamic data but does **not** re-parse `address=`/
   `cname=` directives from `conf-dir` files — those are only read at
   process startup. `_ssh_update()` in `app-multi-zone.py` does a full
   `systemctl restart dnsmasq` (with a pkill+respawn fallback for the
   non-systemd Docker image) for exactly this reason. Confirm the process
   actually restarted:
   ```bash
   ssh debian@192.168.0.231 "sudo journalctl -u dnsmasq -n 5 --no-pager"
   # Should show a fresh "started, version ..." line with a new PID,
   # not just "read /etc/hosts"
   ```

3. **The `keepalive_vip` in `zones.json`/config doesn't match reality.**
   Don't assume the VIP is whatever a doc or default says — confirm against
   the live `keepalived.conf` on the boxes:
   ```bash
   ssh debian@192.168.0.231 "sudo grep -A2 virtual_ipaddress /etc/keepalived/keepalived.conf"
   ```
   A stale/guessed VIP value here has previously caused a real IP collision
   with another host on the network — see the Aug 2026 middle-01 incident
   below.

### Incident: middle-01 record wrong + Deploy not reaching production (Aug 2026)

`middle-01.ad.alshowto.com` resolved to the wrong AAAA record for months
despite the dashboard and `zones.json` showing the correct value. Root
causes, in case a similar symptom shows up again:

- `zones.json`'s `servers` section had been switched to Docker test-cluster
  IPs (`172.20.0.231-233`) during earlier WireGuard-mesh testing and never
  switched back, so every Deploy silently updated a test environment
  instead of `192.168.0.231-233`.
- `_ssh_update()` restarted dnsmasq via `sudo systemctl restart dnsmasq`,
  which briefly got "fixed" to `pkill -HUP` based on debugging done against
  the (non-systemd) Docker test cluster — but the real servers run genuine
  systemd, and `SIGHUP` doesn't reload `address=`/`cname=` records anyway.
- `check_keepalived_status()` had the keepalive VIP hardcoded to a Docker
  address (`172.20.0.252`) instead of reading `zones.json`'s
  `global.keepalive_vip`.
- The AAAA value itself had been guessed/fabricated by an earlier fix
  ("actual Proxmox VM address") without checking `ip a` on the real host,
  and was wrong.
- The real keepalive VIP had previously collided with middle-01's own
  static IP (both briefly `192.168.0.250`) and had to be moved to
  `192.168.0.230` — a good reminder that VIP/static-IP assignments should
  be verified against the live network, not assumed from docs or examples.

Lesson: when a record looks right in `zones.json` but wrong on the wire,
verify against ground truth at every hop — the target server list, the
actual live config file on disk, whether the service actually reloaded it,
and the value itself against the real host — rather than trusting the
previous fix's commit message.

### keepalive check failing

```bash
# Run manual check
ssh debian@192.168.0.231 /usr/local/bin/dnsmasq-monitor.sh

# View cron logs
ssh debian@192.168.0.231 sudo grep CRON /var/log/syslog | tail -20
```

### SSH connection issues

```bash
# Verify SSH access
ssh -v debian@192.168.0.231

# Check SSH key permissions
ls -la ~/.ssh/id_rsa
# Should be 0600 (rw-------)
```

## Performance

- **DNS Queries**: dnsmasq caches queries, minimal latency
- **UI Response**: Sub-second dashboard updates
- **keepalive**: 5-minute check interval, minimal overhead
- **Scaling**: Tested with 100+ DNS records

## License

MIT

## Contributing

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/amazing-feature`)
3. Commit changes (`git commit -m 'Add amazing feature'`)
4. Push to branch (`git push origin feature/amazing-feature`)
5. Open a Pull Request

## Support

- **Issues**: GitHub Issues for bug reports and feature requests
- **Documentation**: See README.md and inline code comments
- **Examples**: Check ansible/ directory for deployment examples

## Roadmap

### Completed ✅
- [x] Multi-zone management UI
- [x] SSH key generation and management
- [x] Password-based SSH authentication
- [x] Reverse proxy support (X-Forwarded headers)
- [x] Configuration dashboard
- [x] Backup & Restore functionality
- [x] Restore & auto-deploy to servers
- [x] Card/Grid view toggle for DNS zones
- [x] Zone record preview in dashboard
- [x] Keepalived status monitoring and display (MASTER/STANDBY/INACTIVE)
- [x] VIP address display and active master indicator
- [x] HA UI deployment with GlusterFS shared storage
- [x] Docker deployment on all DNS servers
- [x] Real-time zones.json replication (replica-3)
- [x] Single VIP for both DNS and UI
- [x] Automatic UI failover with keepalived health checks
- [x] Configurable VIP address in setup script
- [x] WireGuard mesh networking (v2.2) - full-mesh encrypted inter-node communication
- [x] VLAN sub-interface management - persistent multi-subnet presence per server, provisioned live from the Config page
- [x] Group Update Plans - pluggable script-based HA group updates (Proxmox VE VLAN presence first), self-reverting safety net, serialized per-member, hard-stop-and-lock per group on failure

### Planned 📋
- [ ] Zone file import/export
- [ ] DNSSEC support
- [ ] Advanced monitoring dashboard with graphs
- [ ] Backup/restore functionality
- [ ] API authentication/authorization (OAuth2, API keys)
- [ ] Metrics export (Prometheus format)
- [ ] Load balancing across DNS servers
- [ ] Bulk record operations
- [ ] Record templates and macros
- [ ] Audit logging for all changes
- [ ] DNS query analytics and caching stats

---

**Status**: Production Ready (v2.2+)
**Last Updated**: 2026-03-15
**Latest Version**: v2.2 - WireGuard mesh networking, HA UI with GlusterFS, single VIP failover
**Repository**: https://github.com/alpauna/dnsmasq-ui

### What's New in v2.2
- ✨ **WireGuard Full-Mesh**: Encrypted inter-node communication for disconnected networks
- 🔐 **Secure Key Management**: Private keys in gitignored file, public keys in zones.json
- 🚀 **Fleet-wide Deployment**: Deploy mesh to all servers or individual nodes via API
- 📊 **Mesh Health Monitoring**: Check peer connectivity and tunnel status
- 🔗 **Dual Network Support**: Keepalived VIP + WireGuard tunnels work together seamlessly
