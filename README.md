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

## Architecture

### HA Deployment (Recommended)

```
┌──────────────────────────────────────────────────┐
│  192.168.0.250 (Keepalived VIP)                  │
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
#   4. VIP address (default: 192.168.0.250)
#   5. Confirm configuration

# 2. Deploy DNS servers and keepalived
cd ansible
ansible-playbook -i inventory.ini dnsmasq-setup.yml

# 3. Deploy HA UI with GlusterFS and Docker
ansible-playbook -i inventory.ini dnsmasq-ui-ha.yml

# 4. Verify UI is accessible on all servers
curl http://192.168.0.250:5000/api/status     # Via VIP
curl http://192.168.0.231:5000/api/status     # Direct to dns01
curl http://192.168.0.232:5000/api/status     # Direct to dns02
curl http://192.168.0.233:5000/api/status     # Direct to dns03

# 5. Access dashboard in browser
# http://192.168.0.250:5000
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

The dnsmasq-ui dashboard itself can run on all DNS servers with automatic failover using GlusterFS for shared storage.

### What is HA UI Deployment?

Instead of running the UI on a single management server, you can:
- **Run dnsmasq-ui in Docker on all three DNS servers**
- **Share zones.json via GlusterFS** (replica-3 volume = 3 copies)
- **Use the same keepalived VIP** for both DNS (port 53) and UI (port 5000)
- **Automatic failover**: If the MASTER server fails, the VIP moves to a BACKUP, taking both DNS and UI with it

### Quick HA Deployment

After running `setup.sh` and initial DNS deployment:

```bash
# Deploy HA UI with GlusterFS
cd ansible
ansible-playbook -i inventory.ini dnsmasq-ui-ha.yml

# Verify UI is running on all servers
curl http://192.168.0.231:5000/api/status
curl http://192.168.0.232:5000/api/status
curl http://192.168.0.233:5000/api/status

# Access UI via VIP (same IP as DNS)
curl http://192.168.0.250:5000/api/status
```

### HA UI Features

✅ **GlusterFS Replica-3**: All three servers hold a copy of zones.json
✅ **Real-time Sync**: Changes on any server are visible to all
✅ **Single VIP**: Same IP for DNS and UI (different ports)
✅ **Automatic Failover**: If MASTER fails, VIP moves within 1-2 seconds
✅ **UI Health Monitoring**: Keepalived tracks the UI container health
✅ **No Data Loss**: GlusterFS survives 1 server failure

### GlusterFS Details

The playbook sets up:
- **Volume Name**: `dnsmasq-ui`
- **Replication**: 3 copies (replica-3) across all servers
- **Brick Path**: `/data/glusterfs/` on each server
- **Mount Point**: `/opt/dnsmasq-ui-data/` on each server
- **Container Mount**: zones.json bound into Docker container

### Monitoring HA UI

```bash
# Check GlusterFS volume status (on any DNS server)
ssh debian@192.168.0.231 gluster volume status dnsmasq-ui

# Check if UI container is running
ssh debian@192.168.0.231 docker ps | grep dnsmasq-ui

# Check zones.json sync (same across all servers)
ssh debian@192.168.0.231 ls -la /opt/dnsmasq-ui-data/zones.json
ssh debian@192.168.0.232 ls -la /opt/dnsmasq-ui-data/zones.json
ssh debian@192.168.0.233 ls -la /opt/dnsmasq-ui-data/zones.json

# View keepalived health checks
ssh debian@192.168.0.231 sudo systemctl status keepalived
```

### Failover Test

To test automatic UI failover:

```bash
# 1. Verify current MASTER (should be dns01)
curl http://192.168.0.250:5000/api/status

# 2. Stop the UI container on dns01
ssh debian@192.168.0.231 docker compose down
cd /opt/dnsmasq-ui && docker compose down

# 3. Verify VIP moved to dns02 (should happen within 10 seconds)
curl http://192.168.0.250:5000/api/status
# Should now be responding from dns02

# 4. Restart UI on dns01
ssh debian@192.168.0.231 docker compose up -d
cd /opt/dnsmasq-ui && docker compose up -d

# 5. Verify dns01 resumes as MASTER
curl http://192.168.0.250:5000/api/status
# Should respond from dns01 (higher priority)
```

See [HA_UI_DEPLOYMENT.md](HA_UI_DEPLOYMENT.md) for detailed HA setup guide.

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
    "keepalive_vip": "192.168.0.250",
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
ssh debian@192.168.0.231 ip addr | grep 192.168.0.250
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
#         "vip": "192.168.0.250"
#       }
#     }
#   },
#   "vip": "192.168.0.250"
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
The configuration page (`/config`) provides SSH key, server, and dynamic-DNS management:

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
- **Forgot the password**: there's no reset flow by design (single shared
  password, no email). Delete `auth.json` on the server and restart
  `dnsmasq-ui.service` — `/setup` runs again on the next visit.
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
| SSH private key | `~/.ssh/id_rsa` (e.g. `/home/debian/.ssh/id_rsa`) | outside the app directory entirely |
| Deployed dnsmasq config | `/etc/dnsmasq.d/local-records.conf` | on each DNS server, not the dashboard host |

None of the `0600` files above are readable by `git pull`/`push` — they're
gitignored and stay local to whichever server the dashboard runs on.

`smtp.env` format (plain `KEY=value`, no quoting needed for simple values):
```
SMTP_SERVER=mail.example.com
SMTP_PORT=587
SMTP_USER=admin
SMTP_PASSWORD=your-smtp-password
SMTP_FROM=admin@example.com
```
The systemd unit references it via `EnvironmentFile=-/opt/dnsmasq-ui/smtp.env`
(the leading `-` makes it optional — the app starts fine without email 2FA
configured, that feature just won't work until the file exists).

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
    "keepalive_vip": "192.168.0.250",
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
