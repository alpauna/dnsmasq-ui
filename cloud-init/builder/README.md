# Builder VM Cloud-init Configuration

This directory contains cloud-init configuration for the dnsmasq-ui builder VM.

## Configuration Details

- **Hostname**: builder
- **IP Address**: 192.168.0.253/23
- **MAC Address**: bc:24:11:65:e1:01
- **Gateway**: 192.168.0.1
- **DNS**: 192.168.0.250 (dnsmasq-ui VIP), 1.1.1.1, 8.8.8.8

## Files

- **user-data.yml**: System configuration, packages, SSH keys, services
- **network-config.yml**: Network interface configuration with static IP and MAC matching

## What Gets Installed

### Core Services
- Docker and Docker Compose (for testing)
- Ansible (for deployments)
- dnsmasq and keepalived (for local testing)

### Developer Tools
- Git, curl, wget, jq, vim
- Python3 with docker and dotenv modules
- Net-tools, iproute2 for networking

### SSH Configuration
- SSH server and client
- Passwordless sudo for debian user
- SSH authorized_keys pre-populated

## How to Use

### On Proxmox

1. **Create a new Ubuntu 22.04 VM** with:
   - 4 vCPUs (or more)
   - 4GB RAM (or more)
   - 20GB disk
   - MAC address: `bc:24:11:65:e1:01`
   - DHCP initially (or skip networking)

2. **Pass cloud-init files during VM creation**:
   ```bash
   # Mount at boot
   qm set <VM_ID> -cicustom "local:cloud-init/builder/user-data.yml,local:cloud-init/builder/network-config.yml"
   ```

   Or via Proxmox UI:
   - Cloud-Init: User Data → paste contents of user-data.yml
   - Cloud-Init: Network Data → paste contents of network-config.yml

3. **Boot the VM**

4. **Verify networking**:
   ```bash
   ip addr show
   # Should show: 192.168.0.253/23
   ```

### Verify SSH Access

```bash
ssh debian@192.168.0.253
# Should connect without password (key-based auth)
```

### Clone dnsmasq-ui Repository

```bash
cd /opt
sudo git clone https://github.com/alpauna/dnsmasq-ui.git
sudo chown -R debian:debian /opt/dnsmasq-ui
cd /opt/dnsmasq-ui
```

## Test Docker Deployment

Once the VM is running and repository is cloned:

```bash
cd /opt/dnsmasq-ui
./docker/build-test-cluster.sh

# This will:
# 1. Build the DNS node Docker image
# 2. Start 3 containers (dns01, dns02, dns03)
# 3. Configure keepalived with VIP at 172.20.0.250

# Verify:
docker ps                                  # See running containers
dig @172.20.0.250 example.com             # Test DNS
curl http://172.20.0.250:5000/api/status  # Test UI (if running)
```

## Deploy to Actual DNS Servers

After Docker tests pass, deploy to real servers:

```bash
cd /opt/dnsmasq-ui

# 1. Generate configuration
./setup.sh
# Select: 1 (Existing servers)
# Enter your actual server IPs

# 2. Deploy DNS services
cd ansible
ansible-playbook -i inventory.ini dnsmasq-setup.yml

# 3. Deploy HA UI
ansible-playbook -i inventory.ini dnsmasq-ui-ha.yml

# 4. Verify
curl http://192.168.0.250:5000/api/status
```

## Customization

### Add Your SSH Public Key

Edit `user-data.yml` and replace the placeholder:

```yaml
ssh_authorized_keys:
  - ssh-rsa AAAAB3NzaC1yc2E... (your actual public key)
```

Get your public key:
```bash
cat ~/.ssh/id_rsa.pub
```

### Change System Resources

For more demanding deployments, increase:
- **vCPUs**: 8+ for large clusters
- **RAM**: 8-16GB for parallel Ansible runs
- **Disk**: 50GB for multiple Docker images

### Add Additional Tools

Edit `packages:` section in user-data.yml to add:
- `terraform` - Infrastructure as code
- `packer` - Image building
- `jq` - JSON processing
- `tmux` - Terminal multiplexer
- `htop` - System monitoring

## Troubleshooting

### Network Not Configured

If the network config doesn't apply:

```bash
# Check cloud-init logs
cloud-init query --pretty
cat /var/log/cloud-init.log

# Manual network configuration
sudo ip addr add 192.168.0.253/23 dev eth0
sudo ip route add default via 192.168.0.1
sudo systemctl restart networking
```

### Docker Not Starting

```bash
sudo systemctl status docker
sudo systemctl start docker
sudo usermod -aG docker $USER
newgrp docker
```

### No SSH Access

```bash
# Check if SSH is running
sudo systemctl status ssh

# Check authorized_keys
cat ~/.ssh/authorized_keys

# Generate new key if needed
ssh-keygen -t rsa -b 4096
```

## References

- dnsmasq-ui GitHub: https://github.com/alpauna/dnsmasq-ui
- Cloud-init Documentation: https://cloud-init.io/
- Proxmox Cloud-init: https://pve.proxmox.com/wiki/Cloud-init_Support
