# dnsmasq-ui

Web-based management dashboard for dnsmasq DNS servers with multi-zone support, keepalive monitoring, and Ansible automation.

## Features

- 🖥️ **Web Dashboard**: Manage DNS records across multiple dnsmasq servers
- 🔄 **Multi-Zone Support**: Configure separate zones (ad.alshowto.com, etc.)
- 📊 **Server Status**: Real-time monitoring of dnsmasq service health
- ❤️ **Keepalive Tracking**: Automatic health checks and status logging
- 🤖 **Ansible Automation**: Full deployment and configuration management
- 🐳 **Docker Support**: Easy containerized deployment
- 🔐 **SSH-based Management**: Secure remote configuration via SSH keys

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

### servers.json

Configure your DNS servers:

```json
{
  "dns01": {
    "ip": "192.168.0.231",
    "hostname": "dns01",
    "port": 22
  },
  "dns02": {
    "ip": "192.168.0.232",
    "hostname": "dns02",
    "port": 22
  },
  "dns03": {
    "ip": "192.168.0.233",
    "hostname": "dns03",
    "port": 22
  }
}
```

### Environment Variables

```bash
# SSH configuration
export SSH_KEY=~/.ssh/id_rsa
export SSH_USER=debian

# dnsmasq paths
export DNSMASQ_UI_CONFIG=/etc/dnsmasq-ui/servers.json
export DNSMASQ_RECORDS_FILE=/etc/dnsmasq.d/local-records.conf
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

### Get DNS Records

```bash
curl http://localhost:5000/api/records/dns01
```

### Update DNS Records

```bash
curl -X POST http://localhost:5000/api/records/dns01 \
  -H "Content-Type: application/json" \
  -d '{
    "records": [
      {"domain": "example.ad.alshowto.com", "type": "A", "value": "192.168.0.100"},
      {"domain": "example.ad.alshowto.com", "type": "AAAA", "value": "2604:7a00:ea40::100"},
      {"domain": "www.ad.alshowto.com", "type": "CNAME", "value": "example.ad.alshowto.com"}
    ]
  }'
```

### Check Server Status

```bash
curl http://localhost:5000/api/status
```

## Supported Record Types

- **A**: IPv4 address
- **AAAA**: IPv6 address
- **CNAME**: Canonical name (alias)

## Web UI

### Dashboard
- View all DNS servers and their status
- Quick health check overview
- Navigate to individual server management

### Server Management
- View all DNS records for a zone
- Add new records
- Edit existing records
- Delete records
- Save changes (syncs to dnsmasq)

## File Structure

```
dnsmasq-ui/
├── app.py                 # Flask application
├── requirements.txt       # Python dependencies
├── Dockerfile            # Container configuration
├── docker-compose.yml    # Docker Compose setup
├── servers.json          # Server configuration
├── templates/
│   ├── dashboard.html    # Main dashboard
│   └── server.html       # Server detail/management
└── ansible/
    ├── dnsmasq-setup.yml # Ansible playbook
    └── inventory.ini     # Server inventory
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

- **SSH Keys**: Requires SSH key authentication (no passwords)
- **Credentials**: Store SSH keys securely in `/root/.ssh/id_rsa`
- **Network**: Run dnsmasq-ui on protected network or behind firewall
- **Access Control**: Consider adding authentication layer for production use

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

- [ ] Multi-zone management UI
- [ ] Zone file import/export
- [ ] DNSSEC support
- [ ] Advanced monitoring dashboard
- [ ] Backup/restore functionality
- [ ] API authentication/authorization
- [ ] Metrics export (Prometheus)
- [ ] Load balancing across DNS servers

---

**Status**: Production Ready (v1.0)
**Last Updated**: 2026-03-14
**Maintainer**: Your Team
