#!/bin/bash
# Deploy Builder VM using Debian 12 (Bookworm) Cloud Image
# Stable, production-tested alternative with cloud-init package installation
# Based on: https://alshowto.com/proxmox-and-debian-12-cloud-image/
#
# Usage: bash deploy-builder-debian12.sh [.env_file]
# Default VM ID: 9102
# Default IP: 192.168.0.254/23
#
# Advantage: Debian 12 is stable and well-tested
# Note: For latest packages, use deploy-builder-cloud-image.sh (Debian 13)
# This script installs all packages via cloud-init on first boot

set -e

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

log() { echo -e "${BLUE}[*]${NC} $*"; }
success() { echo -e "${GREEN}✓${NC} $*"; }
error() { echo -e "${RED}✗${NC} $*"; exit 1; }

# Load environment
source "${1:-.env}" || error "No .env file found"

# Configuration
PROXMOX_HOST="${PROXMOX_HOST:-192.168.7.13}"
PROXMOX_PASSWORD="${PROXMOX_PASSWORD}"
VM_ID="${VM_ID:-9102}"
VM_NAME="${VM_NAME:-builder-deb12}"
VM_CORES="${VM_CORES:-4}"
VM_MEMORY="${VM_MEMORY:-4096}"
VM_STORAGE="${VM_STORAGE:-c-vm}"
VM_BRIDGE="${VM_NETWORK_BRIDGE:-vmbr0}"
VM_MAC="${VM_MAC_ADDRESS:-bc:24:11:65:e1:03}"
VM_DISK_SIZE=30

CLOUD_INIT_USER="${CLOUD_INIT_USER:-debian}"
CLOUD_INIT_HOSTNAME="${CLOUD_INIT_HOSTNAME:-builder-deb12}"
CLOUD_INIT_IP="${CLOUD_INIT_IP:-192.168.0.254/23}"
CLOUD_INIT_GATEWAY="${CLOUD_INIT_GATEWAY:-192.168.0.1}"
SSH_KEY_PATH="${SSH_KEY_PATH:-$HOME/.ssh/id_rsa.pub}"
# Ensure full path expansion
eval SSH_KEY_PATH="$SSH_KEY_PATH"

header() {
    echo ""
    echo -e "${BLUE}╔════════════════════════════════════════════════════════════╗${NC}"
    echo -e "${BLUE}║${NC} $1"
    echo -e "${BLUE}╚════════════════════════════════════════════════════════════╝${NC}"
    echo ""
}

header "Debian 12 (Bookworm) Cloud Image - Builder VM Deployment"

# Validate SSH key
if [ ! -f "$SSH_KEY_PATH" ]; then
    error "SSH key not found at $SSH_KEY_PATH"
fi

success "SSH key found"

# Display configuration
echo "Configuration:"
echo "  Proxmox Host:    $PROXMOX_HOST"
echo "  VM ID:           $VM_ID"
echo "  VM Name:         $VM_NAME"
echo "  IP/Subnet:       $CLOUD_INIT_IP"
echo "  Gateway:         $CLOUD_INIT_GATEWAY"
echo ""

read -p "Continue with deployment? [y/N]: " confirm
if [[ ! $confirm =~ ^[Yy]$ ]]; then
    error "Deployment cancelled"
fi

# Create deployment script for Proxmox
cat > /tmp/builder-deb12-deploy.sh << 'DEPLOY_SCRIPT'
#!/bin/bash
set -e

# Parameters
VM_ID=$1
VM_NAME=$2
VM_CORES=$3
VM_MEMORY=$4
VM_STORAGE=$5
VM_BRIDGE=$6
VM_MAC=$7
VM_DISK_SIZE=$8
CLOUD_INIT_USER=$9
CLOUD_INIT_HOSTNAME=${10}
CLOUD_INIT_IP=${11}
CLOUD_INIT_GATEWAY=${12}
SSH_PUB_KEY=${13}

DEBIAN_IMAGE_URL="https://cloud.debian.org/images/cloud/bookworm/latest/debian-12-generic-amd64.qcow2"
DEBIAN_IMAGE_FILE="debian-13-generic-amd64.qcow2"
TEMP_DIR=$(mktemp -d)
cd "$TEMP_DIR"

echo "════════════════════════════════════════════════════════════"
echo "  Debian 12 (Bookworm) Cloud Image Deployment"
echo "════════════════════════════════════════════════════════════"
echo ""

# Step 1: Cleanup
echo "[1/11] Cleaning up previous VM..."
qm destroy $VM_ID 2>/dev/null || true
rm -f "$DEBIAN_IMAGE_FILE"
echo "[✓] Cleanup complete"

# Step 2: Install tools
echo "[2/11] Installing libguestfs-tools..."
apt-get update -qq && apt-get install -y -qq libguestfs-tools > /dev/null 2>&1
echo "[✓] Tools installed"

# Step 3: Download image
echo "[3/11] Downloading Debian 12 (Bookworm) cloud image..."
wget -q "$DEBIAN_IMAGE_URL" -O "$DEBIAN_IMAGE_FILE" 2>/dev/null || \
  wget -q "https://cloud.debian.org/images/cloud/bookworm/latest/debian-12-generic-amd64.qcow2" -O "$DEBIAN_IMAGE_FILE"
echo "[✓] Image downloaded"

# Step 4: Minimal image customization (only qemu-guest-agent)
echo "[4/11] Customizing image (qemu-guest-agent only, packages via cloud-init)..."
virt-customize -a "$DEBIAN_IMAGE_FILE" \
  --install qemu-guest-agent \
  --truncate /etc/machine-id \
  > /dev/null 2>&1
echo "[✓] Image customized"

# Step 5: Create VM
echo "[5/11] Creating VM..."
qm create $VM_ID \
  --name "$VM_NAME" \
  --cpu host \
  --machine q35 \
  --memory "$VM_MEMORY" \
  --cores "$VM_CORES" \
  --numa 1 \
  --net0 "virtio=$VM_MAC,bridge=$VM_BRIDGE" \
  --agent enabled=1,fstrim_cloned_disks=1,type=virtio
echo "[✓] VM created"

# Step 6: Import disk
echo "[6/11] Importing disk image..."
qm importdisk $VM_ID "$DEBIAN_IMAGE_FILE" "$VM_STORAGE" -format qcow2 > /dev/null 2>&1
echo "[✓] Disk imported"

# Step 7: Configure storage and boot
echo "[7/11] Configuring storage and boot..."
qm set $VM_ID \
  --scsihw virtio-scsi-pci \
  --scsi0 "$VM_STORAGE:vm-$VM_ID-disk-0" \
  --ide2 "$VM_STORAGE:cloudinit" \
  --boot c \
  --bootdisk scsi0 \
  --serial0 socket \
  --vga serial0 > /dev/null 2>&1
echo "[✓] Storage configured"

# Step 8: Resize disk
echo "[8/11] Resizing disk to ${VM_DISK_SIZE}GB..."
qm resize $VM_ID scsi0 +$((VM_DISK_SIZE - 2))G > /dev/null 2>&1
echo "[✓] Disk resized"

# Step 9: Configure network
echo "[9/11] Configuring network..."
qm set $VM_ID --ipconfig0 "ip=$CLOUD_INIT_IP,gw=$CLOUD_INIT_GATEWAY" > /dev/null 2>&1
echo "[✓] Network configured"

# Step 10: Create cloud-init user-data with packages
echo "[10/11] Creating cloud-init user-data (with package installation)..."

mkdir -p /var/lib/vz/snippets

cat > /var/lib/vz/snippets/builder-deb12-user-data.yml << 'USERDATA'
#cloud-config
hostname: HOSTNAME
manage_etc_hosts: true
fqdn: HOSTNAME.alshowto.com

users:
  - name: USERNAME
    gecos: System Admin
    groups: sudo,docker
    shell: /bin/bash
    sudo: ALL=(ALL) NOPASSWD:ALL
    ssh_authorized_keys:
      - SSH_KEY

# Package installation via cloud-init (faster on first boot)
packages:
  - qemu-guest-agent
  - openssh-server
  - openssh-client
  - curl
  - wget
  - git
  - jq
  - vim
  - net-tools
  - iproute2
  - python3
  - python3-pip
  - python3-docker
  - python3-dotenv
  - docker.io
  - docker-compose
  - ansible
  - dnsmasq
  - keepalived

# Update package manager
package_update: true
package_upgrade: true

# System configuration
timezone: UTC
write_files:
  - path: /etc/sysctl.d/99-custom.conf
    content: |
      net.bridge.bridge-nf-call-iptables=1
      net.bridge.bridge-nf-call-ip6tables=1

# Enable Docker daemon
runcmd:
  - systemctl enable docker
  - systemctl start docker
  - usermod -aG docker USERNAME
  - systemctl enable ssh
  - echo "Cloud-init provisioning complete!"

final_message: "Builder VM ready for dnsmasq-ui testing - Debian 12 (Bookworm)"
USERDATA

# Replace placeholders (use # as delimiter to avoid issues with SSH key slashes)
sed -i "s#HOSTNAME#$CLOUD_INIT_HOSTNAME#g" /var/lib/vz/snippets/builder-deb12-user-data.yml
sed -i "s#USERNAME#$CLOUD_INIT_USER#g" /var/lib/vz/snippets/builder-deb12-user-data.yml
# Escape special characters in SSH key for sed (escape &, \, and # for the # delimiter)
SSH_PUB_KEY_ESCAPED=$(echo "$SSH_PUB_KEY" | sed 's/[&\#]/\\&/g')
sed -i "s#SSH_KEY#$SSH_PUB_KEY_ESCAPED#g" /var/lib/vz/snippets/builder-deb12-user-data.yml

echo "[✓] User-data created"

# Step 11: Link cloud-init
echo "[11/11] Linking cloud-init..."
qm set $VM_ID --cicustom "local:snippets/builder-deb12-user-data.yml" > /dev/null 2>&1 || true
echo "[✓] Cloud-init configured"

# Cleanup
cd /
rm -rf "$TEMP_DIR"

echo ""
echo "════════════════════════════════════════════════════════════"
echo "  ✓ Debian 12 Builder VM Ready!"
echo "════════════════════════════════════════════════════════════"
echo ""
echo "VM: $VM_NAME (ID: $VM_ID)"
echo "IP: $CLOUD_INIT_IP"
echo "User: $CLOUD_INIT_USER (SSH key-based auth)"
echo ""
echo "Packages will be installed automatically on first boot via cloud-init"
echo ""

DEPLOY_SCRIPT

chmod +x /tmp/builder-deb12-deploy.sh

log "Uploading deployment script to $PROXMOX_HOST..."
sshpass -p "$PROXMOX_PASSWORD" scp -o StrictHostKeyChecking=no \
  /tmp/builder-deb12-deploy.sh "root@$PROXMOX_HOST:/tmp/" 2>/dev/null

log "Executing deployment..."
SSH_PUB_KEY=$(cat "$SSH_KEY_PATH")

sshpass -p "$PROXMOX_PASSWORD" ssh -o StrictHostKeyChecking=no "root@$PROXMOX_HOST" \
  "bash /tmp/builder-deb12-deploy.sh $VM_ID '$VM_NAME' $VM_CORES $VM_MEMORY '$VM_STORAGE' '$VM_BRIDGE' '$VM_MAC' $VM_DISK_SIZE '$CLOUD_INIT_USER' '$CLOUD_INIT_HOSTNAME' '$CLOUD_INIT_IP' '$CLOUD_INIT_GATEWAY' '$SSH_PUB_KEY'"

echo ""
header "✓ Debian 12 Deployment Complete!"

echo "Builder VM (Debian 12 / Bookworm) has been deployed!"
echo ""
echo "Deployment Method:"
echo "  ✓ Cloud image: Debian 12 (Bookworm)"
echo "  ✓ Customization: Minimal (only qemu-guest-agent via virt-customize)"
echo "  ✓ Packages: Installed via cloud-init on first boot"
echo "  ✓ SSH: Key-based authentication pre-configured"
echo ""
echo "Start the VM:"
echo "  sshpass -p '$PROXMOX_PASSWORD' ssh -o StrictHostKeyChecking=no root@$PROXMOX_HOST 'qm start $VM_ID'"
echo ""
echo "Access after cloud-init completes (3-5 minutes):"
echo "  ping ${CLOUD_INIT_IP%/*}"
echo "  ssh $CLOUD_INIT_USER@${CLOUD_INIT_IP%/*}"
echo ""

# Auto-start option
read -p "Start VM now? [y/N]: " start_now
if [[ $start_now =~ ^[Yy]$ ]]; then
    log "Starting VM..."
    sshpass -p "$PROXMOX_PASSWORD" ssh -o StrictHostKeyChecking=no root@$PROXMOX_HOST \
      "qm start $VM_ID" 2>/dev/null
    success "VM started - Cloud-init will complete in 3-5 minutes"
fi
