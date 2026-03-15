# All-Docker Architecture Deployment Guide

This guide explains how to deploy dnsmasq-ui with containerized DNS servers on the builder VM.

## Overview

The All-Docker Architecture runs all components (dnsmasq-ui + dns01/dns02/dns03) in Docker containers on a single host, enabling testing without affecting production VMs.

- **Container Network**: 172.20.0.0/24 (internal Docker bridge)
- **VIP (Virtual IP)**: 172.20.0.252 (keepalived failover)
- **dnsmasq-ui**: 172.20.0.10 (manages DNS containers via SSH)
- **DNS Servers**: 172.20.0.231-233 (Master + 2 Backups)

## Prerequisites

- Builder VM (192.168.0.253) with Docker and Docker Compose
- SSH key for container access (~/.ssh/id_rsa or SSH_KEY env var)
- Git repository with latest dnsmasq-ui code

## Deployment Steps

### 1. Pull Latest Code
```bash
cd /opt/dnsmasq-ui
git pull origin master
```

### 2. Set Up Docker Override (First Time Only)
```bash
# Copy the override template for local testing
cp docker-compose.override.example.yml docker-compose.override.yml
```

This tells Docker Compose to use `zones-docker.json` (container IPs) instead of production `zones.json`.

### 3. Build and Start Containers
```bash
# Stop existing containers
docker-compose down

# Rebuild images with latest code
docker-compose build --no-cache

# Start all services
docker-compose up -d

# Verify all containers running
docker-compose ps
```

Expected output:
```
NAME              STATUS
dnsmasq-ui        Up (Healthy)
dns01             Up
dns02             Up
dns03             Up
```

### 4. Verify All-Docker Cluster

#### Check UI health
```bash
curl http://localhost:5000/api/status
# Should return: {"status": "ok"}
```

#### View current zones
```bash
curl http://localhost:5000/api/zones | jq
```

#### Check DNS containers are responding
```bash
# SSH to dns01 (mapped to localhost:2201)
ssh -p 2201 root@localhost 'systemctl status dnsmasq'

# Should show: active (running)
```

#### Verify keepalived VIP
```bash
docker exec dns01 ip addr show eth0
# Should show: 172.20.0.252/24 if master is active
```

## Configuration Files

### zones.json (Production)
- **Usage**: Used by docker-compose.yml by default
- **Server IPs**: 192.168.0.231-233 (production VMs)
- **VIP**: 192.168.0.252
- **Do NOT modify** unless updating production servers

### zones-docker.json (Testing)
- **Usage**: Mounted via docker-compose.override.yml
- **Server IPs**: 172.20.0.231-233 (Docker containers)
- **VIP**: 172.20.0.252
- **Safe to modify** for testing changes

### docker.env (Testing)
- Contains environment variables for Docker containers
- Uses zones-docker.json by default
- Automatically sourced when docker-compose.override.yml is present

## Testing WireGuard Mesh (Optional)

Once All-Docker cluster is running and stable:

```bash
# Generate WireGuard keypairs
curl -X POST http://localhost:5000/api/wireguard/generate-keys | jq

# Validate configuration
curl http://localhost:5000/api/wireguard/validate | jq

# Deploy WireGuard to all containers
curl -X POST http://localhost:5000/api/wireguard/deploy | jq

# Check mesh status
curl http://localhost:5000/api/wireguard/status | jq

# Inside a container, verify peers
docker exec dns01 wg show
```

## Troubleshooting

### Containers won't start
```bash
# Check logs
docker-compose logs dnsmasq-ui
docker-compose logs dns01

# Common issues:
# - Port conflicts (5000, 2201-2203, 5301-5303 already in use)
# - SSH key not found (~/.ssh/id_rsa missing)
# - Insufficient Docker resources (memory/CPU)
```

### SSH to containers fails
```bash
# Verify SSH is listening
docker-compose logs dns01 | grep -i ssh

# Test SSH connection
ssh -vv -p 2201 root@localhost

# Check SSH_KEY env var if key is in non-standard location
export SSH_KEY=/path/to/custom/key
docker-compose restart dnsmasq-ui
```

### keepalived VIP not visible
```bash
# Check keepalived status in Master
docker exec dns01 systemctl status keepalived

# Check logs
docker exec dns01 tail -20 /var/log/syslog | grep -i keepalived

# Verify network capabilities
docker-compose ps dns01
# Should show: CAP_ADD: NET_ADMIN, NET_RAW
```

### DNS queries not working
```bash
# Check dnsmasq is running on all nodes
docker exec dns01 systemctl status dnsmasq
docker exec dns02 systemctl status dnsmasq
docker exec dns03 systemctl status dnsmasq

# Test DNS resolution from host
# First, find VIP of active master
docker exec dns01 ip addr show eth0

# Then query
dig @172.20.0.252 example.ad.alshowto.com
```

## Cleanup

To remove All-Docker cluster and return to production config:

```bash
# Stop and remove containers
docker-compose down

# Remove Docker override
rm docker-compose.override.yml

# Next docker-compose will use production zones.json
```

## Production Deployment

To deploy the same All-Docker setup on production infrastructure:

1. Copy dnsmasq-ui repository to production servers
2. Create docker-compose.override.yml pointing to your production zones.json
3. Configure keepalived VIP for your production network
4. Run `docker-compose up -d` on each server or use orchestration (Kubernetes, Docker Swarm, etc.)

## Related Documentation

- **README.md**: Feature overview and general configuration
- **docker-compose.yml**: All-Docker Architecture service definitions
- **zones-docker.json**: Test configuration with container IPs
- **docker.env**: Environment variables for Docker deployment
