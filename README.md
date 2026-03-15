# dnsmasq-ui

Web-based management dashboard for dnsmasq DNS servers with multi-zone support, keepalive monitoring, and Ansible automation.

## Features

- 🖥️ **Web Dashboard**: Manage DNS records across multiple dnsmasq servers
- 🔄 **Multi-Zone Support**: Configure separate zones (ad.alshowto.com, internal.alshowto.com, etc.)
- 📊 **Server Status**: Real-time monitoring of dnsmasq service health
- ❤️ **Keepalive Tracking**: Automatic health checks and status logging
- 🤖 **Ansible Automation**: Full deployment and configuration management
- 🐳 **Docker Support**: Easy containerized deployment
- 🔐 **SSH Key Management**: Generate, upload, and distribute SSH keys to servers
- 🔑 **Password-based SSH Auth**: Initial setup with user passwords, fallback to key auth
- 🔀 **Reverse Proxy Support**: X-Forwarded headers for deployment behind nginx/Traefik/HAProxy
- 📋 **Configuration Dashboard**: Manage SSH keys and server settings from web UI

## Architecture

```
┌─────────────────────────────────────────┐
│      dnsmasq-ui (Web Dashboard)         │
│  Running on 192.168.0.250 (VIP)         │
└────────┬────────────────────────────────┘
         │
         ├─→ dns01 (192.168.0.231)
         ├─→ dns02 (192.168.0.232)
         └─→ dns03 (192.168.0.233)

All three DNS servers run dnsmasq with synchronized
local DNS records and upstream forwarding.
```

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

### Environment Variables

```bash
# Configuration
export ZONES_CONFIG=zones.json                           # Zone and server config file
export DNSMASQ_RECORDS_FILE=/etc/dnsmasq.d/local-records.conf  # dnsmasq output path

# SSH Configuration
export SSH_KEY=~/.ssh/id_rsa                            # Private key for SSH auth
export SSH_USER=debian                                   # SSH username for servers

# Reverse Proxy Support
export PROXY_PATH_PREFIX=/dnsmasq-ui                    # URL path prefix (optional)
export TRUSTED_PROXIES=*                                # Trusted proxy IPs (or '*' for all)
```

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

## Ansible Deployment

Deploy dnsmasq across all three servers:

```bash
# Install Ansible
pip install ansible

# Configure inventory
cd ansible
vim inventory.ini  # Update IPs and SSH keys

# Run playbook
ansible-playbook -i inventory.ini dnsmasq-setup.yml

# Verify deployment
ansible-playbook -i inventory.ini dnsmasq-setup.yml --extra-vars "verify=true"
```

### Playbook Features

- Installs dnsmasq on all servers
- Configures local DNS records
- Sets up keepalive health checks via cron
- Disables systemd-resolved to avoid conflicts
- Starts and enables dnsmasq service

## Monitoring & Keepalive

Each DNS server runs a health check every 5 minutes:

```bash
# Check local status
cat /var/run/dnsmasq-status

# View health history
tail -f /var/log/dnsmasq-monitor.log

# Manual health check
/usr/local/bin/dnsmasq-monitor.sh
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

# Check server status
curl http://localhost:5000/api/status
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

## Supported Record Types

- **A**: IPv4 address
- **AAAA**: IPv6 address
- **CNAME**: Canonical name (alias)

## Web UI

### Dashboard
- View all DNS servers and their status (online/offline indicators)
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

### Configuration Page
The configuration page (`/config`) provides SSH key and server management:

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
- **Access Control**: Consider adding authentication layer (e.g., reverse proxy auth) for production use
- **Reverse Proxy**: Full support for X-Forwarded headers when deployed behind nginx/Traefik/HAProxy
- **Logs**: Client IP tracking via X-Forwarded-For headers automatically logged

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

**Status**: Production Ready (v2.0)
**Last Updated**: 2026-03-15
**Latest Version**: v2.0 - Multi-zone with SSH key management
**Repository**: https://github.com/alpauna/dnsmasq-ui
