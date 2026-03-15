# dnsmasq-ui Interactive Setup Guide

## Overview

The interactive setup script (`setup.sh`) automates the configuration of your DNS cluster for any number of servers. It handles:

- SSH user configuration
- DNS server address specification (individual IPs, ranges, or lists)
- SSH connectivity validation
- Dynamic Ansible inventory generation
- Keepalived configuration with automatic priority assignment
- zones.json configuration updates

## Quick Start

```bash
# Run the interactive setup
./setup.sh

# Follow the prompts to configure your cluster
# The script will generate all necessary configuration files
```

## Setup Scenarios

### Scenario 1: 3 Servers with IP Range

```
Number of DNS servers [3]: 3
Enter server address(es): 192.168.0.231-233
```

**Generates:**
- dns01 (192.168.0.231) - MASTER, priority 150
- dns02 (192.168.0.232) - BACKUP, priority 140
- dns03 (192.168.0.233) - BACKUP, priority 130

### Scenario 2: 5 Servers with Comma-Separated List

```
Number of DNS servers [3]: 5
Enter server address(es): 10.0.0.10, 10.0.0.11, 10.0.0.12, 10.0.0.13, 10.0.0.14
```

**Generates:**
- dns01 (10.0.0.10) - MASTER, priority 150
- dns02 (10.0.0.11) - BACKUP, priority 140
- dns03 (10.0.0.12) - BACKUP, priority 130
- dns04 (10.0.0.13) - BACKUP, priority 120
- dns05 (10.0.0.14) - BACKUP, priority 110

### Scenario 3: Single Server Setup

```
Number of DNS servers [3]: 1
Enter server address(es): 192.168.1.100
```

**Generates:**
- dns01 (192.168.1.100) - MASTER, priority 150

## Input Formats

### Single IP Address
```
192.168.0.231
```

### IP Range (expands from start to end)
```
192.168.0.231-235
# Expands to: 192.168.0.231, .232, .233, .234, .235
```

### Comma-Separated List
```
192.168.0.231, 192.168.0.240, 192.168.1.50
# Whitespace is automatically trimmed
```

### Mixed (just specify one format)
```
# Valid:
192.168.0.231-233
10.0.0.10, 10.0.0.11
192.168.0.100

# Invalid (don't mix formats):
192.168.0.231-233, 10.0.0.10  # ❌ Don't mix range and list
```

## Setup Process

### 1. SSH User Configuration
```
SSH user for DNS servers [debian]: ubuntu
```

Choose the SSH user that has sudo access on your DNS servers.
- Default: `debian`
- Common alternatives: `ubuntu`, `root`, `ansible`

### 2. Number of Servers
```
Number of DNS servers [3]: 5
```

Specify how many DNS servers you want to deploy.
- Minimum: 1 (single server, no failover)
- Recommended: 3+ (high availability)
- Maximum: Limited only by your infrastructure

### 3. Server Addresses
```
Enter server address(es): 192.168.0.231-233
```

Provide server addresses in one of three formats:
- Single IP: `192.168.0.231`
- Range: `192.168.0.231-233` (expands to .231, .232, .233)
- List: `192.168.0.231, 192.168.0.232, 192.168.0.233`

### 4. SSH Connectivity Test
```
Testing SSH Connectivity
✓ SSH to dns01 (192.168.0.231): OK
✓ SSH to dns02 (192.168.0.232): OK
✓ SSH to dns03 (192.168.0.233): OK
```

The script verifies SSH access to each server. If any server is unreachable:
- You can review the SSH configuration and try again
- Or continue anyway (useful if servers aren't online yet)

### 5. Configuration Summary
```
Configuration Summary
SSH User:         debian
Number of Servers: 3

Servers:
  dns01 (192.168.0.231): MASTER [priority: 150]
  dns02 (192.168.0.232): BACKUP [priority: 140]
  dns03 (192.168.0.233): BACKUP [priority: 130]
```

Review the configuration. The script shows:
- Server names (auto-generated: dns01, dns02, etc.)
- IP addresses
- Keepalived role (MASTER or BACKUP)
- Keepalived priority (higher = more likely to be master)

### 6. Configuration Generation
```
Generating Configurations
✓ Inventory generated at ansible/inventory.ini
✓ Ansible playbook generated at ansible/dnsmasq-setup.yml
✓ zones.json updated with 3 servers
```

The script generates/updates:
- `ansible/inventory.ini` - Server definitions for Ansible
- `ansible/dnsmasq-setup.yml` - Playbook with dynamic keepalived config
- `zones.json` - Application configuration with new servers

## Generated Files

### ansible/inventory.ini

Contains all DNS servers in Ansible-compatible format:

```ini
[dns_servers]
dns01 ansible_host=192.168.0.231 ansible_user=debian ansible_become=yes
dns02 ansible_host=192.168.0.232 ansible_user=debian ansible_become=yes
dns03 ansible_host=192.168.0.233 ansible_user=debian ansible_become=yes

[dns_servers:vars]
ansible_python_interpreter=/usr/bin/python3
ansible_ssh_private_key_file=~/.ssh/id_rsa
```

### ansible/dnsmasq-setup.yml

Updated Ansible playbook with:
- Dynamic keepalived priority assignment based on server position
- Keepalived automatic state selection (first server = MASTER, rest = BACKUP)
- Configurable VIP and virtual router ID
- dnsmasq installation and configuration
- Health check monitoring

### zones.json

Updated with new server configuration:

```json
{
  "zones": [...],
  "servers": {
    "dns01": {"ip": "192.168.0.231", "hostname": "dns01", "port": 22, "enabled": true},
    "dns02": {"ip": "192.168.0.232", "hostname": "dns02", "port": 22, "enabled": true},
    "dns03": {"ip": "192.168.0.233", "hostname": "dns03", "port": 22, "enabled": true}
  },
  "global": {...}
}
```

## Deployment Options

After setup, deploy using one of these methods:

### Option 1: Ansible Playbook (Recommended for Production)
```bash
cd ansible
ansible-playbook -i inventory.ini dnsmasq-setup.yml
```

**Benefits:**
- Idempotent (safe to run multiple times)
- Detailed logging of each step
- Rollback support
- Excellent for configuration management

### Option 2: Deployment Script (Quick & Simple)
```bash
./deploy-keepalived.sh all
```

**Benefits:**
- Single command
- No Ansible required
- Shows progress and status
- Good for quick deployments

### Option 3: Manual SSH (For Individual Testing)
```bash
ssh debian@192.168.0.231 "sudo apt-get install -y dnsmasq keepalived"
ssh debian@192.168.0.231 "sudo systemctl start dnsmasq"
```

**Benefits:**
- Full control
- Useful for debugging
- Server-by-server deployment

## Keepalived Priority System

The setup script automatically assigns priorities based on server order:

```
Number of Servers → Priorities
1 server:         150 (MASTER only)
2 servers:        150, 140 (MASTER, BACKUP)
3 servers:        150, 140, 130 (MASTER, BACKUP, BACKUP)
4 servers:        150, 140, 130, 120
5 servers:        150, 140, 130, 120, 110
```

**How it works:**
1. First server (dns01) = MASTER with priority 150
2. Other servers = BACKUP with decreasing priorities
3. If MASTER fails, highest-priority BACKUP takes over
4. When MASTER recovers, it automatically resumes the MASTER role

## Verification After Deployment

### Check Cluster Status
```bash
curl http://dns-server:5000/api/status | python3 -m json.tool
```

Expected output:
```json
{
  "servers": {
    "dns01": {
      "online": true,
      "keepalived": {"status": "MASTER", "running": true}
    },
    "dns02": {
      "online": true,
      "keepalived": {"status": "STANDBY", "running": true}
    }
  },
  "vip": "192.168.0.250"
}
```

### Check Keepalived Status
```bash
ssh debian@192.168.0.231 sudo systemctl status keepalived
ssh debian@192.168.0.231 ip addr show | grep 192.168.0.250
```

### Check DNS Resolution
```bash
dig @192.168.0.250 @any-configured-domain.com
dig @192.168.0.231 @any-configured-domain.com
dig @192.168.0.232 @any-configured-domain.com
```

## Troubleshooting

### Issue: Setup hangs on SSH connectivity test
**Solution:**
- Verify SSH key is at `~/.ssh/id_rsa`
- Check server is reachable: `ping 192.168.0.231`
- Test SSH manually: `ssh debian@192.168.0.231 echo OK`

### Issue: Invalid IP address error
**Solution:**
- Use valid IPv4 format: `192.168.0.231`
- Avoid DNS names: use IPs only
- For ranges: `192.168.0.231-233` (end octet only)

### Issue: Ansible playbook fails after setup
**Solution:**
- Install Ansible: `pip install ansible`
- Verify inventory: `cat ansible/inventory.ini`
- Test connectivity: `ansible all -i ansible/inventory.ini -m ping`

### Issue: Keepalived not starting on servers
**Solution:**
- Check service status: `ssh debian@IP sudo systemctl status keepalived`
- Check logs: `ssh debian@IP sudo journalctl -u keepalived -f`
- Verify config: `ssh debian@IP sudo cat /etc/keepalived/keepalived.conf`

### Issue: VIP not assigned to MASTER
**Solution:**
- Ensure dnsmasq is running: `systemctl status dnsmasq`
- Check interface name: `ip link show` (may not be eth0)
- Update keepalived.conf with correct interface and retest

## Advanced Usage

### Reconfiguring Cluster Size
To change the number of servers later:

```bash
# Run setup again
./setup.sh

# Provide different server configuration
# Setup will regenerate all files with new settings
```

### Using Different SSH Keys
The setup uses the default `~/.ssh/id_rsa`. To use a different key:

```bash
# Before running setup
export SSH_KEY=~/.ssh/custom_key
./setup.sh

# Or manually update ansible/inventory.ini:
# ansible_ssh_private_key_file=~/.ssh/custom_key
```

### Custom VIP Address
Edit the generated `ansible/dnsmasq-setup.yml` and change:

```yaml
vars:
  keepalive_vip: 192.168.0.250  # Change this
```

Then redeploy:
```bash
cd ansible
ansible-playbook -i inventory.ini dnsmasq-setup.yml
```

### Adding Servers Later

To add more servers to an existing cluster:

```bash
# Run setup again with new number
./setup.sh

# Provide all server addresses (including existing ones)
# Ansible will only update changed servers (idempotent)

cd ansible
ansible-playbook -i inventory.ini dnsmasq-setup.yml
```

## Best Practices

1. **Always test SSH before setup**
   - Ensure passwordless SSH works for all servers
   - Use SSH key authentication, not passwords

2. **Use static IPs**
   - Never use DHCP for DNS servers
   - Static IPs ensure predictable behavior

3. **Use IP ranges when possible**
   - Cleaner than listing 20 individual IPs
   - Less error-prone than comma-separated lists

4. **Start with 3 servers minimum**
   - High availability requires at least 2 servers
   - 3 is recommended for production

5. **Review generated configs**
   - Always check `ansible/inventory.ini` for correct IPs
   - Review `ansible/dnsmasq-setup.yml` for keepalived settings
   - Verify `zones.json` has all servers

6. **Test deployment on one server first**
   ```bash
   # Deploy to single server for testing
   ./deploy-keepalived.sh dns01
   ```

7. **Use VIP for DNS queries**
   - Point clients to the VIP (192.168.0.250 by default)
   - Not individual server IPs
   - Enables transparent failover

## Example Deployments

### Small Office (3 Servers)
```bash
./setup.sh
# Servers: 192.168.0.231-233
# SSH User: debian
# Result: 3-server cluster with automatic failover
```

### Enterprise (10 Servers)
```bash
./setup.sh
# Servers: 10.0.1.100-109
# SSH User: sysadmin
# Result: 10-server cluster with cascading failover
```

### Multi-Region (5 Servers in 2 Regions)
```bash
./setup.sh
# Servers: 192.168.0.231-233, 10.0.1.50, 10.0.1.51
# SSH User: ubuntu
# Result: 5-server cluster spanning regions
```

## Getting Help

### View Generated Files
```bash
cat ansible/inventory.ini      # Server definitions
cat ansible/dnsmasq-setup.yml  # Playbook
cat zones.json                 # App config
```

### Run Deployment Tests
```bash
cd tests
./dns-stress-test.sh            # Test DNS performance
./run-all-tests.sh --failover   # Test failover behavior
```

### Check Logs
```bash
# dnsmasq logs
ssh debian@SERVER sudo tail -f /var/log/dnsmasq.log

# keepalived logs
ssh debian@SERVER sudo journalctl -u keepalived -f

# dnsmasq-ui API status
curl http://SERVER:5000/api/status
```
