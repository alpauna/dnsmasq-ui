# Keepalived Deployment Guide

## Overview

Keepalived provides high availability for DNS services using VRRP (Virtual Router Redundancy Protocol). This configuration uses a priority-based master/backup selection:

- **dns01** (192.168.0.231): MASTER, priority 150 - holds the VIP when healthy
- **dns02** (192.168.0.232): BACKUP, priority 140 - takes over if dns01 fails
- **dns03** (192.168.0.233): BACKUP, priority 130 - lowest priority backup
- **VIP**: 192.168.0.250 (Virtual IP that moves between servers)

## Deployment Methods

### Method 1: Deployment Script (Recommended)

The `deploy-keepalived.sh` script automates the entire keepalived setup process:

```bash
# Deploy to all servers
./deploy-keepalived.sh all

# Deploy to specific server
./deploy-keepalived.sh dns01

# Show help
./deploy-keepalived.sh --help
```

**What the script does:**
1. Installs keepalived and iproute2 packages
2. Generates keepalived.conf with correct priorities and state
3. Enables and starts the keepalived service
4. Verifies VIP assignment (MASTER only)
5. Reports final cluster status

**Advantages:**
- Single command to deploy all servers
- Automatic verification after each step
- No Ansible installation required
- Produces detailed logs of each step
- Works over SSH to remote servers

### Method 2: Ansible Playbook

If you have Ansible installed:

```bash
cd ansible
ansible-playbook -i inventory.ini dnsmasq-setup.yml
```

**Playbook features:**
- Installs dnsmasq, keepalived, and dependencies
- Configures keepalived with VRRP cluster
- Sets up dnsmasq with DNS records
- Enables and starts all services

**Note:** Update `inventory.ini` with correct server IPs and SSH key path before running.

### Method 3: Manual Setup

For single server or troubleshooting:

```bash
# Install packages
ssh debian@192.168.0.231
sudo apt-get install -y keepalived iproute2

# Create keepalived.conf
sudo tee /etc/keepalived/keepalived.conf << 'EOF'
global_defs {
  router_id DNS_CLUSTER
  script_user root
}

vrrp_instance DNS_VIP {
  state MASTER
  interface eth0
  virtual_router_id 51
  priority 150
  advert_int 1

  virtual_ipaddress {
    192.168.0.250/24
  }

  track_processes {
    dnsmasq
  }
}
EOF

# Start service
sudo systemctl enable keepalived
sudo systemctl restart keepalived
```

## Configuration Details

### Keepalived Config Structure

```
global_defs
  ├── router_id: Cluster identifier
  ├── script_user: User for running scripts

vrrp_instance DNS_VIP
  ├── state: MASTER or BACKUP
  ├── interface: Network interface (eth0)
  ├── virtual_router_id: VRRP group ID (51)
  ├── priority: 150 (MASTER), 140 (BACKUP-1), 130 (BACKUP-2)
  ├── advert_int: Advertisement interval (1 second)
  ├── virtual_ipaddress: VIP with subnet (192.168.0.250/24)
  └── track_processes: Services to monitor (dnsmasq)
```

### Priority System

**Higher priority = more likely to be MASTER**

- dns01: priority 150 → MASTER (default, holds VIP)
- dns02: priority 140 → BACKUP (takes over if dns01 is down)
- dns03: priority 130 → BACKUP (last resort)

If dns01 fails:
1. dns02 detects failure (via dnsmasq process tracking)
2. dns02 priority becomes effective
3. dns02 becomes MASTER and acquires VIP
4. When dns01 recovers, it becomes MASTER again (higher priority)

## Verification

### Check Service Status

```bash
# On each server, verify keepalived is running
ssh debian@192.168.0.231 sudo systemctl status keepalived

# Check VIP assignment (should only show on MASTER)
ssh debian@192.168.0.231 ip addr show | grep 192.168.0.250
```

### Monitor via dnsmasq-ui API

```bash
curl http://192.168.0.233:5000/api/status | python3 -m json.tool
```

**Expected output:**
```json
{
  "servers": {
    "dns01": {
      "keepalived": {
        "status": "MASTER",
        "running": true,
        "vip": "192.168.0.250"
      }
    },
    "dns02": {
      "keepalived": {
        "status": "STANDBY",
        "running": true,
        "vip": "192.168.0.250"
      }
    },
    "dns03": {
      "keepalived": {
        "status": "STANDBY",
        "running": true,
        "vip": "192.168.0.250"
      }
    }
  },
  "vip": "192.168.0.250"
}
```

### Dashboard Display

- **Green badge (MASTER)**: Server holds the VIP and is actively serving DNS
- **Orange badge (STANDBY)**: Server is ready to take over if needed
- **Gray badge (INACTIVE)**: Keepalived is not running

## Failover Testing

To test failover without disrupting service:

```bash
# Simulate dns01 failure (on dns01)
ssh debian@192.168.0.231
sudo systemctl stop keepalived

# Monitor api/status to see dns02 become MASTER
curl http://192.168.0.233:5000/api/status

# Verify VIP moved to dns02
ssh debian@192.168.0.232 ip addr show | grep 192.168.0.250

# Restore dns01
ssh debian@192.168.0.231
sudo systemctl start keepalived
```

## Troubleshooting

### Keepalived not starting

```bash
# Check logs
ssh debian@192.168.0.231 sudo systemctl status keepalived -l

# Validate config syntax
ssh debian@192.168.0.231 sudo keepalived -t

# Check keepalived.conf permissions
ssh debian@192.168.0.231 ls -la /etc/keepalived/keepalived.conf
# Should show: -rw-r--r-- root root
```

### VIP not assigned to MASTER

```bash
# Verify interface name (may not be eth0)
ssh debian@192.168.0.231 ip link show
# Update keepalived.conf with correct interface name

# Check dnsmasq is running (tracked process)
ssh debian@192.168.0.231 sudo systemctl status dnsmasq

# Verify VRRP router_id is unique
ssh debian@192.168.0.231 sudo systemctl status keepalived | grep router
```

### All servers show STANDBY

```bash
# Check if all servers have wrong state
ssh debian@192.168.0.231 sudo cat /etc/keepalived/keepalived.conf | grep -A 20 "vrrp_instance"

# Verify priorities are different on each server
for ip in 192.168.0.231 192.168.0.232 192.168.0.233; do
  ssh debian@$ip "grep priority /etc/keepalived/keepalived.conf"
done
```

## Configuration Files

- **Keepalived config**: `/etc/keepalived/keepalived.conf`
- **Keepalived logs**: `sudo journalctl -u keepalived -f`
- **dnsmasq-ui monitoring**: `http://[dns03]:5000/api/status`

## Failover Recovery

Keepalived uses **preemption** by default:

- When MASTER comes back online, it automatically becomes MASTER again (higher priority)
- No manual intervention needed
- Service remains active during transitions
- DNS queries route to the current MASTER/VIP

## Performance Tuning

### Advertisement Interval (advert_int)

```
advert_int 1  # Current: Advertisements every 1 second
```

- **Lower values** (0.5s): Faster failover detection, more network traffic
- **Higher values** (3s+): Slower failover detection, less network traffic
- **Current setting**: 1 second (good balance)

### Process Tracking

```
track_processes {
  dnsmasq
}
```

Keepalived monitors dnsmasq and demotes to BACKUP if it dies. Adjust to track other critical services if needed.

## Related Documentation

- [Keepalived Official Docs](http://www.keepalived.org/)
- [VRRP Protocol (RFC 3768)](https://tools.ietf.org/html/rfc3768)
- dnsmasq-ui Dashboard: `http://[host]:5000/`
- dnsmasq-ui API: `/api/status` endpoint
