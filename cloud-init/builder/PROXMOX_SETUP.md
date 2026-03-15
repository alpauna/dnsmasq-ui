# Builder VM Setup on Proxmox

Step-by-step guide to create the builder VM and test Docker deployment.

## Step 1: Prepare Cloud-init Files

The cloud-init files are ready in this directory:
- `user-data.yml` - System configuration
- `network-config.yml` - Network configuration

## Step 2: Create VM on Proxmox (Via Web UI)

### 2a. Create a new VM
1. In Proxmox Web UI → Click **Create VM**
2. Fill in details:
   - **Node**: Select your node
   - **VM ID**: 100 (or next available)
   - **Name**: builder
   - **Resource Pool**: (optional)

### 2b. General Settings
1. **Machine**: pc
2. **BIOS**: SeaBIOS
3. **EFI Storage**: (leave empty)

### 2c. OS Settings
1. **Storage**: local
2. **ISO Image**: ubuntu-22.04-live-server-amd64.iso
   - (Download if not available)

### 2d. System Settings
1. **Cores**: 4
2. **Type**: host
3. **CPU Units**: 1024
4. **Memory**: 4096 MB (4GB)
5. **Swap**: 0

### 2e. Hard Disk
1. **Storage**: local-lvm
2. **Disk size**: 30 GB
3. **Cache**: Default (writeback)
4. **Discard**: enabled
5. **SSD emulation**: checked

### 2f. Network
1. **Bridge**: vmbr0
2. **MAC address**: `bc:24:11:65:e1:01`
3. **Model**: VirtIO (paravirtualized)
4. **Firewall**: (optional)

### 2g. Cloud-init
**This is important!**

1. **User**: debian
2. **SSH public key**: (paste your SSH public key - `cat ~/.ssh/id_rsa.pub`)
3. **Password**: (optional, can leave empty if using SSH key)
4. **DNS domain**: ap.alshowto.com
5. **Cloud-Init type**: Qemu Agent
6. **Cloud-Init storage**: local-lvm

Click **Create** to finish

---

## Step 3: Configure Cloud-init via CLI (Alternative)

If the Web UI cloud-init isn't working:

```bash
# On Proxmox host
VM_ID=100

# Copy cloud-init files to Proxmox
scp cloud-init/builder/user-data.yml root@proxmox-ip:/tmp/
scp cloud-init/builder/network-config.yml root@proxmox-ip:/tmp/

# SSH to Proxmox
ssh root@proxmox-ip

# On Proxmox host:
VM_ID=100
mkdir -p /var/lib/vz/snippets

# Create cloud-init config
cat > /var/lib/vz/snippets/builder-user-data.yml << 'EOF'
# Contents of user-data.yml
EOF

cat > /var/lib/vz/snippets/builder-network-config.yml << 'EOF'
# Contents of network-config.yml
EOF

# Link to VM config
qm set $VM_ID -cicustom "local:snippets/builder-user-data.yml"
qm set $VM_ID -cicustom "local:snippets/builder-network-config.yml"
```

---

## Step 4: Boot and Install Ubuntu

1. Select the builder VM in Proxmox
2. Click **Start**
3. Open **Console** and follow Ubuntu 22.04 installation
   - Accept defaults where possible
   - When prompted for cloud-init, select "Enable cloud-init"
   - Set hostname to `builder` (though cloud-init will override this)
   - Use DHCP initially (cloud-init will set static IP)

### 4a. Installation Steps
```
Welcome to Ubuntu 22.04
→ Select language: English
→ Keyboard layout: English
→ Network configuration: (DHCP for now, cloud-init will fix it)
→ Proxy: (skip)
→ Ubuntu archive mirror: (default)
→ File system setup: Use an entire disk
→ Confirm destructive action: Continue
→ Profile setup:
   - Your name: Debian User
   - Your server's name: builder
   - Pick a username: debian
   - Choose a password: (can skip, using SSH keys)
→ SSH Setup:
   - Import SSH identity: Yes (paste your public key)
→ Featured Server Snaps: (skip)
→ Installation starts...
```

### 4b. Wait for completion
- Installation takes 2-5 minutes
- When done, click **Reboot Now**

---

## Step 5: Boot the VM

After reboot:
1. VM should boot and apply cloud-init
2. Cloud-init will:
   - Set hostname to `builder`
   - Configure network (192.168.0.253/24)
   - Install Docker, Ansible, Git, etc.
   - Set up SSH keys
   - Enable services

This takes 3-5 minutes.

---

## Step 6: Verify VM is Ready

```bash
# Check VM status in Proxmox console
# Should see login prompt and cloud-init messages

# Once booted, verify from your machine
ping 192.168.0.253
# Should get responses

ssh debian@192.168.0.253
# Should connect without password (key-based auth)
```

---

## Step 7: Clone Repository and Test Docker

Once logged in via SSH:

```bash
# Clone the repository
cd /opt
sudo git clone https://github.com/alpauna/dnsmasq-ui.git
sudo chown -R debian:debian /opt/dnsmasq-ui

cd /opt/dnsmasq-ui

# Verify the Docker test cluster files exist
ls -la docker/

# Build and start the test cluster
cd docker
./build-test-cluster.sh
```

Expected output:
```
=================================================================
dnsmasq-ui Docker Test Cluster
=================================================================

Building DNS node image...
Starting test cluster (dns01, dns02, dns03)...

Test Cluster Running
Network: dnsmasq-net (172.20.0.0/24)

Servers:
  dns01: 172.20.0.231 (SSH: ssh root@172.20.0.231)
  dns02: 172.20.0.232 (SSH: ssh root@172.20.0.232)
  dns03: 172.20.0.233 (SSH: ssh root@172.20.0.233)
  VIP:   172.20.0.250 (Keepalived managed)
```

---

## Step 8: Test Docker Cluster

```bash
# Check containers are running
docker ps

# Test DNS via VIP
dig @172.20.0.250 example.com

# View logs
docker logs dns01
docker logs dns02
docker logs dns03

# Access keepalived VIP
ping 172.20.0.250
# Should respond (from the container network)
```

---

## Step 9: Verify Keepalived VIP Assignment

```bash
# SSH into dns01 container
docker exec -it dns01 bash

# Check VIP is assigned to MASTER (dns01)
ip addr | grep 172.20.0.250
# Should show: inet 172.20.0.250/24 scope global secondary eth0

# Check keepalived status
systemctl status keepalived
# Should show: active (running)

# Test DNS from inside container
dig @172.20.0.250 example.com
# Should resolve
```

---

## Step 10: Test Failover (Optional)

To simulate master failure:

```bash
# In builder VM, stop dnsmasq on dns01
docker exec dns01 systemctl stop dnsmasq

# Monitor keepalived status
docker exec dns01 systemctl status keepalived
# Should show BACKUP (VIP moved to dns02)

# Restart dnsmasq
docker exec dns01 systemctl start dnsmasq

# Monitor status
docker exec dns01 systemctl status keepalived
# Should resume MASTER (highest priority)
```

---

## Troubleshooting

### VM stuck on cloud-init

```bash
# SSH to Proxmox host
ssh root@proxmox-ip

# Check cloud-init logs in VM
qm terminal 100  # VM ID

# Inside VM, check cloud-init
cloud-init status
cloud-init show-json
tail -f /var/log/cloud-init.log
```

### Network not configured

```bash
# Check cloud-init applied network
cat /etc/netplan/99-cloud-init.yaml

# Manually set network
sudo ip addr add 192.168.0.253/24 dev eth0
sudo ip route add default via 192.168.0.1

# Or reboot
sudo reboot
```

### Docker not installed

```bash
# Install manually
sudo apt-get update
sudo apt-get install -y docker.io docker-compose
sudo usermod -aG docker debian
newgrp docker
```

### SSH not working

```bash
# Check SSH is running
sudo systemctl status ssh

# Check authorized_keys
cat ~/.ssh/authorized_keys

# Add your key if missing
echo "ssh-rsa AAAA..." >> ~/.ssh/authorized_keys
```

---

## Next Steps After Testing

Once Docker cluster is tested and working:

1. **Deploy to Real DNS Servers**:
   ```bash
   cd /opt/dnsmasq-ui
   ./setup.sh          # Configure for your servers
   cd ansible
   ansible-playbook -i inventory.ini dnsmasq-setup.yml
   ansible-playbook -i inventory.ini dnsmasq-ui-ha.yml
   ```

2. **Stop Docker Cluster**:
   ```bash
   cd /opt/dnsmasq-ui/docker
   docker compose -f dns-cluster.yml down
   ```

3. **Keep Builder VM Running** as your deployment hub for future use

---

## Quick Reference Commands

```bash
# SSH to builder
ssh debian@192.168.0.253

# Start Docker cluster
cd /opt/dnsmasq-ui/docker && ./build-test-cluster.sh

# Stop Docker cluster
docker compose -f dns-cluster.yml down

# Check cluster status
docker ps
dig @172.20.0.250 example.com

# View logs
docker logs dns01

# Deploy to real servers
cd /opt/dnsmasq-ui && ./setup.sh
```
