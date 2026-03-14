# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What is dnsmasq-ui?

dnsmasq-ui is a web-based management dashboard for dnsmasq DNS servers with multi-zone support, keepalive monitoring, and Ansible automation. It manages DNS records across multiple servers and allows organizing records into separate zones (e.g., ad.alshowto.com, internal.alshowto.com).

## Core Architecture

### Application Layer
- **Location**: `app-multi-zone.py` (primary application)
- **Framework**: Flask with CORS support
- **Purpose**: HTTP API and web dashboard for DNS management
- **Key Components**:
  - `ZoneManager`: Class that manages zones, records, server configuration, and deployments
  - Routes: Zone CRUD, record CRUD, deployment, status checking
  - SSH integration via Paramiko for remote configuration updates

### Zone Manager
- **Location**: `ZoneManager` class in `app-multi-zone.py`
- **Purpose**: Central management of zones, records, and server synchronization
- **Key Methods**:
  - Zone management: `add_zone()`, `delete_zone()`, `get_zone()`, `get_zones()`
  - Record management: `add_record()`, `update_record()`, `delete_record()`
  - Configuration: `generate_dnsmasq_config()` (converts zones to dnsmasq format), `save_config()`
  - Deployment: `deploy_to_servers()` (syncs to all DNS servers), `_ssh_update()` (single server SSH)
  - Status: `check_server_status()` (verify dnsmasq running)

### Configuration Files
- **zones.json**: Defines DNS zones, records, servers, and global settings
  - Zones: Separate domain zones with their own record sets
  - Servers: List of dnsmasq servers with IP, hostname, enabled status
  - Global: Upstream DNS, keepalive VIP, health check interval
- **Environment Variables**:
  - `ZONES_CONFIG`: Path to zones.json (default: 'zones.json')
  - `DNSMASQ_RECORDS_FILE`: Remote file path (default: '/etc/dnsmasq.d/local-records.conf')
  - `SSH_KEY`: Private key for SSH auth (default: '~/.ssh/id_rsa')
  - `SSH_USER`: SSH username (default: 'debian')

### Infrastructure Automation
- **Location**: `ansible/`
- **Playbook**: `dnsmasq-setup.yml` — installs dnsmasq, configures records, sets up keepalive monitoring
- **Inventory**: `inventory.ini` — defines dns01, dns02, dns03 servers

## Development Setup

### Prerequisites
- Python 3.11+
- pip or virtual environment manager
- SSH key for target servers at `~/.ssh/id_rsa` (or via `SSH_KEY` env var)

### Install Dependencies
```bash
pip install -r requirements.txt
```

### Run Development Server
```bash
export ZONES_CONFIG=zones.json
python app-multi-zone.py
# Access dashboard at http://localhost:5000
```

### Run with Docker
```bash
docker-compose up -d
# Access at http://localhost:5000
```

## Key Conventions

### Code Style
- **Language**: Python 3.11+
- **Framework**: Flask (minimal, request-focused)
- **SSH**: Paramiko for remote execution
- **Format**: PEP 8 style (use `black` or similar for consistency)

### Record Types
- **A**: IPv4 address (format: `address=/domain/ip`)
- **AAAA**: IPv6 address (format: `address=/domain/ipv6`)
- **CNAME**: Alias (format: `cname=alias,target`)

### Zone Organization
- Each zone is a separate DNS domain (e.g., ad.alshowto.com, internal.alshowto.com)
- Zones contain their own set of records
- All zones share the same set of servers for redundancy
- Servers sync all zone records into a single dnsmasq config

### File Structure
```
dnsmasq-ui/
├── app-multi-zone.py         # Main Flask application (production)
├── app.py                     # Simple single-server version (reference)
├── zones.json                 # Zone and server configuration
├── requirements.txt           # Python dependencies
├── Dockerfile                 # Container configuration
├── docker-compose.yml         # Docker Compose setup
├── README.md                  # Comprehensive documentation
├── CLAUDE.md                  # This file
├── templates/
│   ├── dashboard-v2.html     # Multi-zone dashboard (TODO)
│   └── zone.html             # Zone detail/record management (TODO)
└── ansible/
    ├── dnsmasq-setup.yml     # Ansible deployment playbook
    └── inventory.ini         # Server inventory
```

### API Endpoints
- `GET /` — Dashboard (zones and servers overview)
- `GET /zone/<zone_name>` — Zone detail page
- `GET /api/zones` — List all zones
- `POST /api/zones` — Create new zone
- `DELETE /api/zones/<zone_name>` — Delete zone
- `GET /api/zones/<zone_name>/records` — Get records in zone
- `POST /api/zones/<zone_name>/records` — Add record to zone
- `DELETE /api/zones/<zone_name>/records/<domain>/<type>` — Delete record
- `POST /api/deploy` — Deploy config to all servers
- `GET /api/status` — Get server status

## Common Development Tasks

### Add a New DNS Record Programmatically
```python
manager.add_record('ad.alshowto.com', 'example.ad.alshowto.com', 'A', '192.168.0.100')
manager.save_config()
```

### Create a New Zone
```python
manager.add_zone('prod.alshowto.com', 'Production domain', 'local')
```

### Deploy Changes to All Servers
```python
results = manager.deploy_to_servers()
print(results)  # {'dns01': {'success': True, 'message': '...'}, ...}
```

### Check Server Status
```python
status = manager.check_server_status('192.168.0.231')
print(status)  # True or False
```

### Generate dnsmasq Configuration
```python
config = manager.generate_dnsmasq_config()
print(config)  # dnsmasq format: address=/domain/ip, cname=...
```

## Security Considerations

- **SSH Authentication**: Uses SSH key-based authentication (no passwords)
- **Private Key**: Stored at `~/.ssh/id_rsa` or via `SSH_KEY` env var
- **SSH User**: Must have `sudo` privileges for systemctl and config file access
- **Command Injection**: Current SSH implementation uses string concatenation (echo + tee) — be careful with special characters in domain names
- **Network**: Run dnsmasq-ui on protected network or behind firewall
- **Authentication**: Consider adding auth layer (e.g., Flask-Login) for production use
- **Firewall**: Restrict DNS queries to trusted clients; keep dnsmasq-ui port restricted

## Ansible Deployment

### Setup
```bash
cd ansible
# Update inventory.ini with correct IPs and SSH key path
ansible-playbook -i inventory.ini dnsmasq-setup.yml
```

### Verify Deployment
```bash
ansible dns_servers -i inventory.ini -m command -a "sudo systemctl status dnsmasq"
```

### Manual Keepalive Check
```bash
ssh debian@192.168.0.231 /usr/local/bin/dnsmasq-monitor.sh
```

## Testing & Validation

### Manual DNS Testing
```bash
# From any client pointing to dnsmasq servers:
dig @192.168.0.231 example.ad.alshowto.com
dig @192.168.0.231 example.ad.alshowto.com AAAA

# Verify upstream forwarding:
dig @192.168.0.231 google.com
```

### API Testing
```bash
# Get zones
curl http://localhost:5000/api/zones

# Create zone
curl -X POST http://localhost:5000/api/zones \
  -H "Content-Type: application/json" \
  -d '{"name": "test.alshowto.com", "description": "Test zone", "type": "local"}'

# Add record
curl -X POST http://localhost:5000/api/zones/test.alshowto.com/records \
  -H "Content-Type: application/json" \
  -d '{"domain": "www.test.alshowto.com", "type": "A", "value": "192.168.0.100"}'

# Deploy
curl -X POST http://localhost:5000/api/deploy

# Check status
curl http://localhost:5000/api/status
```

## Known Limitations & TODOs

- **Web UI Templates**: `dashboard-v2.html` and `zone.html` need to be created for multi-zone management
- **Input Validation**: Records are not validated (domain format, IP addresses, etc.)
- **Error Handling**: SSH failures return basic error strings; could be more descriptive
- **Concurrency**: No locking on zones.json; concurrent writes could corrupt config
- **Rollback**: No built-in rollback if deployment fails on one server
- **Record Deduplication**: No check for duplicate records within a zone

## Related Documentation

- **README.md**: Full feature list, configuration examples, troubleshooting
- **ansible/dnsmasq-setup.yml**: Server setup and keepalive monitoring script
- **zones.json**: Example zone and server configuration

## Quick Reference

### Start development
```bash
python app-multi-zone.py
```

### Docker deployment
```bash
docker-compose up -d
```

### Deploy configuration changes
```bash
curl -X POST http://localhost:5000/api/deploy
```

### View current zones
```bash
curl http://localhost:5000/api/zones | jq
```

---

**Project**: dnsmasq-ui (Multi-zone DNS management dashboard)
**Status**: Development (v1.0 foundation, templates pending)
**Python**: 3.11+
**Primary Dependencies**: Flask, Paramiko, Flask-CORS
