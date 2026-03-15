# Builder VM Quick Start Guide

## One-Command Deploy

```bash
# Debian 13 (latest packages, recommended)
bash ansible/deploy-builder-cloud-image.sh

# OR Debian 12 (stable alternative)
bash ansible/deploy-builder-debian12.sh
```

## Quick Setup

```bash
# 1. Initialize secrets
bash setup-secrets.sh

# 2. Configure environment
source .env

# 3. Deploy builder VM (choose one)
bash ansible/deploy-builder-cloud-image.sh      # Debian 12
# OR
bash ansible/deploy-builder-debian13.sh         # Debian 13
```

## After Deployment

```bash
# SSH into builder VM
ssh debian@192.168.0.253

# Wait for cloud-init to finish
cloud-init status

# Once done, verify services
docker --version
ansible --version
python3 --version
```

## Run Docker Test Cluster

```bash
ssh debian@192.168.0.253 << 'EOF'
cd /opt/dnsmasq-ui/docker
./build-test-cluster.sh
EOF
```

## Debian Version Comparison

| Aspect | Debian 13 | Debian 12 |
|--------|-----------|-----------|
| **Script** | `deploy-builder-cloud-image.sh` (default) | `deploy-builder-debian12.sh` |
| **VM ID** | 9100 | 9102 |
| **IP** | 192.168.0.253/23 | 192.168.0.254/23 |
| **MAC** | bc:24:11:65:e1:01 | bc:24:11:65:e1:03 |
| **Status** | Latest packages (recommended) | Production tested |
| **Package Install** | cloud-init only | cloud-init only |

## Common Commands

```bash
# Check VM status on Proxmox
qm status 9100

# SSH with specific key
ssh -i ~/.ssh/id_rsa debian@192.168.0.253

# Check cloud-init progress
ssh debian@192.168.0.253 'cloud-init status'

# View cloud-init logs
ssh debian@192.168.0.253 'sudo tail -50 /var/log/cloud-init-output.log'

# Run Docker commands on builder VM
ssh debian@192.168.0.253 'docker ps'

# Copy files to builder VM
scp /local/file debian@192.168.0.253:/remote/path
```

## Troubleshooting

### SSH Connection Refused
- Wait for cloud-init to complete: `cloud-init status`
- Check VM is running: `qm status 9100`
- Verify network connectivity: `ping 192.168.0.253`

### Permission Denied (publickey)
- Ensure SSH public key is at `~/.ssh/id_rsa.pub`
- Verify it matches the key in `.env`: `cat ~/.ssh/id_rsa.pub`
- Wait for cloud-init to complete (key is injected during boot)

### Docker Not Found
- Cloud-init is still installing packages
- Check: `cloud-init status`
- Wait 3-5 minutes for large package list to complete

## Full Documentation

See [BUILDER_SETUP.md](BUILDER_SETUP.md) for complete setup procedure, troubleshooting, and advanced configuration.
