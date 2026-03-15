# High Availability dnsmasq-ui Deployment Guide

This guide covers deploying dnsmasq-ui on all three DNS servers with automatic failover using GlusterFS shared storage.

## Overview

### What is HA UI Deployment?

By default, dnsmasq-ui runs on a single server. If that server fails, the UI becomes unavailable even though DNS continues working via keepalived VIP.

**HA UI Deployment solves this by:**
- Running dnsmasq-ui in Docker on all three servers
- Sharing zones.json via GlusterFS (replica-3 = all 3 servers have a copy)
- Using the same keepalived VIP for both DNS (port 53) and UI (port 5000)
- Automatic failover when any component fails

### Architecture

```
192.168.0.250 (Keepalived VIP)
  :53   → dnsmasq (DNS)
  :5000 → dnsmasq-ui (Dashboard)
          ↓
    ┌─────┼─────┐
    ↓     ↓     ↓
  dns01 dns02 dns03
  (MASTER) (BACKUP) (BACKUP)
```

**Each server runs:**
- dnsmasq (DNS service)
- keepalived (VIP failover)
- dnsmasq-ui (Flask app in Docker)
- GlusterFS (replicated storage)

**Shared Storage:**
- GlusterFS replica-3 volume
- Mount: `/opt/dnsmasq-ui-data/`
- Contains: `zones.json` (synced in real-time across all servers)

## Prerequisites

1. **Three DNS servers** with dnsmasq and keepalived already running
   - Recommended: DNS deployment completed via `setup.sh` and `dnsmasq-setup.yml`

2. **Ansible** installed on management machine
   ```bash
   pip install ansible
   ```

3. **SSH access** to all DNS servers
   - Key-based auth preferred (already configured if using dnsmasq-setup.yml)

4. **Docker** will be installed automatically
   - The playbook installs docker.io and required Python bindings

## Deployment Steps

### Step 1: Generate Configuration (if not done)

```bash
cd /home/al-pauna/OpenClaw/dnsmasq-ui

# Run interactive setup
./setup.sh

# Answer prompts:
# SSH user: debian
# Number of servers: 3
# Server addresses: 192.168.0.231-233
# VIP address: 192.168.0.250

# This generates:
# - ansible/inventory.ini
# - ansible/dnsmasq-setup.yml
# - zones.json
```

### Step 2: Deploy DNS Servers (if not done)

```bash
cd ansible

# Deploy dnsmasq and keepalived
ansible-playbook -i inventory.ini dnsmasq-setup.yml

# This should take 2-3 minutes
# Verify with: curl http://192.168.0.250:53/status (for DNS check)
```

### Step 3: Deploy HA UI

```bash
cd ansible

# Deploy GlusterFS, Docker, and dnsmasq-ui
ansible-playbook -i inventory.ini dnsmasq-ui-ha.yml

# This should take 5-10 minutes (includes Docker image download)
```

### Step 4: Verify Deployment

```bash
# Check UI on individual servers
curl http://192.168.0.231:5000/api/status
curl http://192.168.0.232:5000/api/status
curl http://192.168.0.233:5000/api/status

# Check UI via VIP
curl http://192.168.0.250:5000/api/status

# Access dashboard in browser
# http://192.168.0.250:5000
```

## Post-Deployment Verification

### 1. Check GlusterFS Volume

```bash
# SSH to any server
ssh debian@192.168.0.231

# View volume status
sudo gluster volume status dnsmasq-ui

# Expected output:
# Status of volume: dnsmasq-ui
# Gluster process                             Hostname        Port    Online  Pid
# Brick dns01:/data/glusterfs                 dns01           49152   Y       ####
# Brick dns02:/data/glusterfs                 dns02           49152   Y       ####
# Brick dns03:/data/glusterfs                 dns03           49152   Y       ####
# ...
```

### 2. Check Docker Containers

```bash
# Check on each server
ssh debian@192.168.0.231 docker ps | grep dnsmasq-ui
ssh debian@192.168.0.232 docker ps | grep dnsmasq-ui
ssh debian@192.168.0.233 docker ps | grep dnsmasq-ui

# Expected: dnsmasq-ui container running on all three
```

### 3. Verify zones.json Sync

```bash
# Check on all servers (should be identical)
ssh debian@192.168.0.231 cat /opt/dnsmasq-ui-data/zones.json
ssh debian@192.168.0.232 cat /opt/dnsmasq-ui-data/zones.json
ssh debian@192.168.0.233 cat /opt/dnsmasq-ui-data/zones.json

# All should show the same content and have same timestamp
```

### 4. Check Keepalived Status

```bash
# View VIP assignment
ssh debian@192.168.0.231 ip addr | grep 192.168.0.250

# Should show VIP is assigned to MASTER (dns01)
# inet 192.168.0.250/24 scope global secondary eth0

# Check keepalived service
ssh debian@192.168.0.231 sudo systemctl status keepalived

# Should show: active (running)
```

## Testing HA Features

### Test 1: DNS Still Works During UI Failure

```bash
# 1. Query DNS via VIP (working baseline)
dig @192.168.0.250 dns01.ad.alshowto.com
# Should return: 192.168.0.231

# 2. Stop UI on dns01
ssh debian@192.168.0.231 docker compose down
cd /opt/dnsmasq-ui && docker compose down

# 3. DNS should still work on VIP
dig @192.168.0.250 dns01.ad.alshowto.com
# Should still return: 192.168.0.231

# 4. Restart UI
ssh debian@192.168.0.231 docker compose up -d
cd /opt/dnsmasq-ui && docker compose up -d
```

### Test 2: UI Failover When Master Fails

```bash
# 1. Verify current master (should be dns01)
curl http://192.168.0.250:5000/api/status
# Look for "online": true and keepalived status

# 2. Stop dnsmasq (simulates DNS service failure)
ssh debian@192.168.0.231 sudo systemctl stop dnsmasq

# 3. Within 10 seconds, VIP should move to dns02
curl http://192.168.0.250:5000/api/status
# Should now respond from dns02

# 4. Restart dnsmasq on dns01
ssh debian@192.168.0.231 sudo systemctl start dnsmasq

# 5. dns01 should resume as MASTER
curl http://192.168.0.250:5000/api/status
# Should now respond from dns01
```

### Test 3: zones.json Real-Time Sync

```bash
# 1. Add a DNS record via UI
# Access http://192.168.0.250:5000
# Navigate to zone management
# Add new record: test.ad.alshowto.com → 192.168.0.100 (A record)
# Click Deploy

# 2. Verify on all servers (should be identical)
ssh debian@192.168.0.231 cat /opt/dnsmasq-ui-data/zones.json | grep test
ssh debian@192.168.0.232 cat /opt/dnsmasq-ui-data/zones.json | grep test
ssh debian@192.168.0.233 cat /opt/dnsmasq-ui-data/zones.json | grep test

# All should show: "domain": "test.ad.alshowto.com", "value": "192.168.0.100"
```

### Test 4: Handle GlusterFS Brick Failure

```bash
# 1. Stop GlusterFS on one server (simulates disk failure)
ssh debian@192.168.0.233 sudo systemctl stop glusterd

# 2. Volume should still be accessible with 2 remaining bricks
ssh debian@192.168.0.231 sudo gluster volume status dnsmasq-ui

# Expected: Shows dns01 and dns02 online, dns03 offline

# 3. Data is still accessible
curl http://192.168.0.250:5000/api/status
# Should work (reading from dns01 or dns02 copy)

# 4. Restart glusterd to heal
ssh debian@192.168.0.233 sudo systemctl start glusterd

# 5. Volume should self-heal
ssh debian@192.168.0.231 sudo gluster volume status dnsmasq-ui
# All three bricks should show online
```

## Troubleshooting

### Issue: UI container fails to start

**Symptoms:**
```bash
docker ps | grep dnsmasq-ui
# Container not listed
```

**Solution:**
```bash
# Check logs
docker logs dnsmasq-ui

# Check if GlusterFS mount is working
df /opt/dnsmasq-ui-data
# Should show glusterfs volume

# Check zones.json is present
ls -la /opt/dnsmasq-ui-data/zones.json

# Restart Docker
sudo systemctl restart docker
docker compose up -d
```

### Issue: GlusterFS volume stuck in degraded state

**Symptoms:**
```bash
gluster volume status dnsmasq-ui
# Some bricks showing offline
```

**Solution:**
```bash
# Check which brick is down
gluster volume status dnsmasq-ui detail

# If dns01 brick is down:
ssh debian@192.168.0.231 sudo systemctl restart glusterd

# Force heal
gluster volume heal dnsmasq-ui full

# Monitor healing progress
gluster volume heal dnsmasq-ui info
```

### Issue: VIP not assigned to MASTER

**Symptoms:**
```bash
ip addr | grep 192.168.0.250
# No result on any server
```

**Solution:**
```bash
# Check keepalived status
sudo systemctl status keepalived

# Check configuration
sudo cat /etc/keepalived/keepalived.conf

# Verify interface exists
ip link show eth0

# If interface name is different, update keepalived.conf:
# vrrp_instance DNS_VIP {
#   interface <correct-name>
# ...
# Then restart:
sudo systemctl restart keepalived
```

### Issue: UI health check failing (VIP keeps moving)

**Symptoms:**
```bash
# VIP moves between servers every 10 seconds
curl http://192.168.0.250:5000/api/status
# Alternates between dns01, dns02, dns03
```

**Solution:**
```bash
# Check if UI is actually running
docker ps | grep dnsmasq-ui

# Check if port 5000 is responding
curl http://localhost:5000/api/status

# If port not responding, check logs
docker logs dnsmasq-ui

# Ensure curl is installed (needed for health check script)
sudo apt-get install curl

# Restart keepalived after fixing
sudo systemctl restart keepalived
```

### Issue: zones.json not syncing across servers

**Symptoms:**
```bash
# Files have different content/timestamps
ssh debian@192.168.0.231 stat /opt/dnsmasq-ui-data/zones.json
ssh debian@192.168.0.232 stat /opt/dnsmasq-ui-data/zones.json
# Different modification times
```

**Solution:**
```bash
# Check GlusterFS is mounted
df /opt/dnsmasq-ui-data

# Check mount is in fstab
grep glusterfs /etc/fstab

# Check GlusterFS health
gluster volume heal dnsmasq-ui full

# Monitor replication
gluster volume heal dnsmasq-ui info

# If stuck, force a resync
# Edit zones.json on MASTER and save it
ssh debian@192.168.0.231 touch /opt/dnsmasq-ui-data/zones.json
```

## Monitoring

### Key Metrics to Monitor

**1. VIP Assignment**
```bash
# Should always be assigned to one server
ip addr | grep 192.168.0.250
```

**2. Service Health**
```bash
# Both services should be active
sudo systemctl status dnsmasq
sudo systemctl status keepalived
docker ps | grep dnsmasq-ui
```

**3. GlusterFS Status**
```bash
# All bricks online
gluster volume status dnsmasq-ui

# No split-brain
gluster volume info dnsmasq-ui
```

**4. DNS Resolution**
```bash
# Test regularly
dig @192.168.0.250 example.ad.alshowto.com
```

**5. UI Availability**
```bash
# Via VIP
curl http://192.168.0.250:5000/api/status

# Via each server
curl http://192.168.0.231:5000/api/status
curl http://192.168.0.232:5000/api/status
curl http://192.168.0.233:5000/api/status
```

### Automated Monitoring

Consider setting up:
- **Prometheus** to scrape the `/api/status` endpoint
- **Grafana** for visualization
- **Alerting** when VIP changes or services go down
- **Log aggregation** (ELK, Loki) for centralized logs

## Maintenance

### Updating dnsmasq-ui

```bash
# Update the repository on all servers
cd /opt/dnsmasq-ui
git pull

# Restart containers
docker compose down
docker compose up -d
```

### Adding DNS Records

All changes to zones.json are automatically replicated:
1. Access UI via VIP: http://192.168.0.250:5000
2. Add/edit records
3. Click Deploy
4. Changes immediately replicate to all servers via GlusterFS
5. dnsmasq reloads automatically

### Backup

zones.json is automatically replicated 3 times, but you should also:
```bash
# Regular backup to management machine
scp debian@192.168.0.231:/opt/dnsmasq-ui-data/zones.json ./zones-backup-$(date +%Y%m%d).json

# Or via API
curl http://192.168.0.250:5000/api/config/backup > zones-backup-$(date +%Y%m%d).json
```

## Performance Tuning

### GlusterFS Performance

```bash
# Check network latency between servers
ping dns02
# Should be < 5ms for local network

# Monitor GlusterFS operations
ssh debian@192.168.0.231 gluster volume profile dnsmasq-ui start
# Run operations...
ssh debian@192.168.0.231 gluster volume profile dnsmasq-ui info
```

### Docker Memory

If container uses too much memory:
```bash
# Check current usage
docker stats dnsmasq-ui

# Edit docker-compose.yml to add limits:
# services:
#   dnsmasq-ui:
#     mem_limit: 512m
#     memswap_limit: 512m

# Restart
docker compose down && docker compose up -d
```

## Migration Path

### From Single UI to HA UI

If you already have a single-server UI setup:

1. **Backup current zones.json**
   ```bash
   cp zones.json zones-backup.json
   ```

2. **Run HA deployment playbook**
   ```bash
   ansible-playbook -i ansible/inventory.ini ansible/dnsmasq-ui-ha.yml
   ```

3. **Verify zones.json is synced**
   ```bash
   ssh debian@192.168.0.231 cat /opt/dnsmasq-ui-data/zones.json
   ```

4. **Point clients to VIP**
   - Update DNS client settings to use VIP (192.168.0.250)
   - UI now accessible at http://192.168.0.250:5000

## Disaster Recovery

### Recover from Total Failure

If all three servers fail and recover simultaneously:

```bash
# GlusterFS will auto-heal
ssh debian@192.168.0.231 sudo gluster volume status dnsmasq-ui

# Monitor healing
gluster volume heal dnsmasq-ui info

# Wait for all bricks to come online (can take 5-10 minutes for large zones.json)

# Restart services
ssh debian@192.168.0.231 sudo systemctl restart docker
ssh debian@192.168.0.231 sudo systemctl restart keepalived
```

### Recover from zones.json Corruption

```bash
# Restore from backup
scp zones-backup.json debian@192.168.0.231:/opt/dnsmasq-ui-data/zones.json

# GlusterFS will replicate to other servers automatically

# Restart UI if needed
ssh debian@192.168.0.231 docker compose restart dnsmasq-ui
```

## References

- [GlusterFS Documentation](https://docs.glusterfs.org/)
- [Keepalived Documentation](https://www.keepalived.org/)
- [Docker Compose Documentation](https://docs.docker.com/compose/)
- [dnsmasq-ui README](README.md)
- [Setup Guide](SETUP_GUIDE.md)

## Support

For issues or questions:
1. Check troubleshooting section above
2. Review logs: `docker logs dnsmasq-ui`
3. Check GlusterFS status: `gluster volume status`
4. Review GitHub issues: https://github.com/alpauna/dnsmasq-ui/issues
