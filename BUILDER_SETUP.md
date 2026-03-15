# Builder VM Setup Procedure

This document describes how to deploy and configure the dnsmasq-ui builder VM on Proxmox.

## Overview

The builder VM is a Debian cloud-init based VM that provides a test environment for:
- Running Docker test clusters
- Testing Ansible playbooks
- Developing dnsmasq-ui infrastructure
- Building and testing DNS server configurations

Two deployment options are available:
- **Debian 13 (Trixie)**: Latest packages, recommended (default)
- **Debian 12 (Bookworm)**: Stable alternative, for production environments

## Prerequisites

1. **Environment Setup**
   ```bash
   cd /path/to/dnsmasq-ui
   bash setup-secrets.sh
   source .env
   ```
   This creates:
   - `.env` file with Proxmox credentials
   - Ansible vault configuration
   - SSH key validation

2. **SSH Key**
   - Must have SSH key at `~/.ssh/id_rsa.pub`
   - Script validates it exists before deployment
   - This key will be used for `debian` user authentication

3. **Proxmox Access**
   - SSH access to Proxmox (pve3 is default)
   - Root credentials in `.env` file
   - Network connectivity to 192.168.0.0/23 subnet

## Deployment Steps

### Step 1: Choose Debian Version

**Debian 13 (Recommended - Latest packages):**
```bash
bash ansible/deploy-builder-cloud-image.sh
```

**Debian 12 (Stable alternative):**
```bash
bash ansible/deploy-builder-debian12.sh
```

### Step 2: Configure Deployment

The script will prompt for:
- Proxmox host: (default: 192.168.7.13)
- VM ID: (default: 9100 for Debian 12, 9101 for Debian 13)
- VM name: (default: builder)
- Storage: (default: c-vm)
- IP/Subnet: (default: 192.168.0.253/23)
- Gateway: (default: 192.168.0.1)

Review configuration and confirm with `y`.

### Step 3: Deployment Execution

The script will:
1. Download Debian cloud image (~300MB)
2. Customize with qemu-guest-agent
3. Create VM with specified parameters
4. Import disk to Proxmox storage
5. Configure boot and storage
6. Create cloud-init user-data with SSH key
7. Configure network (static IP via netplan)

**Estimated time:** 5-10 minutes

### Step 4: First Boot & Cloud-init

When prompted, start the VM:
```
Start VM now? [y/N]: y
```

Cloud-init will:
1. Configure hostname and FQDN
2. Set up debian user with passwordless sudo
3. Configure SSH authorized keys
4. Install packages (apt-get update, upgrade, install)
5. Enable and start Docker daemon
6. Enable SSH, dnsmasq, keepalived services

**Cloud-init duration:** 3-5 minutes for large package list

### Step 5: Verify Access

Once cloud-init completes:

```bash
# Test connectivity
ping 192.168.0.253

# SSH access
ssh debian@192.168.0.253

# Check VM status
ssh debian@192.168.0.253 'cloud-init status'

# Verify Docker
ssh debian@192.168.0.253 'docker --version'
```

## After Deployment

### Verify Services

```bash
ssh debian@192.168.0.253

# Check systemd services
systemctl status docker       # Should be active
systemctl status ssh          # Should be active
systemctl status dnsmasq      # May need configuration
systemctl status keepalived   # May need configuration
```

### Clone dnsmasq-ui

```bash
ssh debian@192.168.0.253 << 'EOF'
cd /opt
sudo git clone https://github.com/alpauna/dnsmasq-ui.git
sudo chown -R debian:debian /opt/dnsmasq-ui
EOF
```

### Run Docker Test Cluster

```bash
ssh debian@192.168.0.253 << 'EOF'
cd /opt/dnsmasq-ui/docker
./build-test-cluster.sh
EOF
```

This will:
- Build DNS node Docker image
- Create 3-node DNS cluster (dns01, dns02, dns03)
- Configure keepalived for VIP (172.20.0.250)
- Test DNS queries

## Configuration Details

### Debian 13 vs Debian 12

| Feature | Debian 13 | Debian 12 |
|---------|-----------|-----------|
| Image URL | trixie/latest | bookworm/latest |
| Package Install | cloud-init only | cloud-init only |
| Deploy Time | 5-10 min (packages during boot) | 5-10 min |
| Cloud-init Duration | 3-5 min (package install) | 3-5 min |
| VM ID Default | 9100 | 9102 |
| Status | Latest packages | Stable/tested |
| Recommended | ✓ Yes | For stable environments |

### Cloud-init Configuration

The scripts generate a cloud-config YAML with:

```yaml
#cloud-config
hostname: builder
users:
  - name: debian
    ssh_authorized_keys:
      - <your public key>
    groups: sudo,docker
    sudo: ALL=(ALL) NOPASSWD:ALL
packages:
  - docker.io
  - ansible
  - dnsmasq
  - keepalived
  - git
  - curl
  - wget
  - jq
  - and more...
```

### Network Configuration

- Static IP: 192.168.0.253/23
- Gateway: 192.168.0.1
- MAC: bc:24:11:65:e1:01 (Debian 12) or bc:24:11:65:e1:02 (Debian 13)
- Netplan configured during cloud-init

## Troubleshooting

### SSH Key Authentication Fails

**Problem:** `Permission denied (publickey)`

**Solution:**
1. Wait for cloud-init to complete: `cloud-init status`
2. Verify SSH key location: `ls -la ~/.ssh/id_rsa.pub`
3. Check that public key (not private) is embedded in cloud-init

### Cloud-init Still Running After 10 Minutes

**Problem:** Packages still installing

**Check progress:**
```bash
ssh debian@192.168.0.253 'ps aux | grep apt'
ssh debian@192.168.0.253 'sudo tail -f /var/log/cloud-init-output.log'
```

**Wait time:** Large package lists (ansible, docker) can take 5+ minutes

### Dnsmasq Fails to Start

**Problem:** Port 53 already in use

**Cause:** systemd-resolved is running by default on Debian

**Solution:**
```bash
# On builder VM
sudo systemctl stop systemd-resolved
sudo systemctl disable systemd-resolved

# Then start dnsmasq
sudo systemctl start dnsmasq
```

### Keepalived Shows No Config

**Expected behavior:** Keepalived requires `/etc/keepalived/keepalived.conf`

**For Docker test cluster:** Config is generated by Docker containers, not on host

## Support & Debugging

### Check Proxmox VM Status

```bash
# From local machine
sshpass -p 'PASSWORD' ssh -o StrictHostKeyChecking=no root@192.168.7.13 \
  'qm status 9100'

# Check cloud-init config
sshpass -p 'PASSWORD' ssh -o StrictHostKeyChecking=no root@192.168.7.13 \
  'qm cloudinit dump 9100 user'

# View VM logs
sshpass -p 'PASSWORD' ssh -o StrictHostKeyChecking=no root@192.168.7.13 \
  'qm monitor 9100'
```

### Check Cloud-init Logs on VM

```bash
ssh debian@192.168.0.253 'sudo tail -100 /var/log/cloud-init-output.log'
ssh debian@192.168.0.253 'sudo tail -100 /var/log/cloud-init.log'
```

### Manual SSH Key Injection (if needed)

If cloud-init fails to inject SSH key:

```bash
# Copy key manually
ssh debian@192.168.0.253 mkdir -p ~/.ssh
cat ~/.ssh/id_rsa.pub | ssh debian@192.168.0.253 'cat >> ~/.ssh/authorized_keys'
ssh debian@192.168.0.253 'chmod 600 ~/.ssh/authorized_keys'
```

## Next Steps

1. **Deploy Docker Test Cluster**
   - Run `./docker/build-test-cluster.sh`
   - Test DNS queries via VIP

2. **Test Ansible Playbooks**
   - Update `ansible/inventory.ini` with DNS servers
   - Run `ansible-playbook -i inventory.ini dnsmasq-setup.yml`

3. **Develop Infrastructure**
   - Clone dnsmasq-ui to `/opt/dnsmasq-ui`
   - Make configuration changes
   - Test with Docker cluster before production deployment

## Related Documentation

- [Secrets Management Guide](SECRETS_MANAGEMENT.md)
- [Cloud-init User-data](cloud-init/builder/)
- [Ansible Playbooks](ansible/)
- [Docker Test Cluster](docker/README.md)
