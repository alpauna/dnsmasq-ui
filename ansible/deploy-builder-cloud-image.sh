#!/bin/bash
# Deploy Builder VM using Debian 12 Cloud Image
# Based on: https://alshowto.com/proxmox-and-debian-12-cloud-image/
# Fast, automated deployment with cloud-init

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
warning() { echo -e "${YELLOW}⚠${NC} $*"; }

# Load environment
source "${1:-.env}" || error "No .env file found"

# Configuration from environment
PROXMOX_HOST="${PROXMOX_HOST:-192.168.7.13}"
PROXMOX_USER="${PROXMOX_USER:-root}"
PROXMOX_PASSWORD="${PROXMOX_PASSWORD}"
PROXMOX_NODE="${PROXMOX_NODE:-pve3}"

VM_ID="${VM_ID:-9101}"
VM_NAME="${VM_NAME:-builder}"
VM_CORES="${VM_CORES:-4}"
VM_MEMORY="${VM_MEMORY:-4096}"
VM_STORAGE="${VM_STORAGE:-c-vm}"
VM_BRIDGE="${VM_NETWORK_BRIDGE:-vmbr0}"
VM_MAC="${VM_MAC_ADDRESS:-bc:24:11:65:e1:01}"
VM_DISK_SIZE=30

# Cloud-init settings
CLOUD_INIT_USER="${CLOUD_INIT_USER:-debian}"
CLOUD_INIT_HOSTNAME="${CLOUD_INIT_HOSTNAME:-builder}"
CLOUD_INIT_DOMAIN="${CLOUD_INIT_DOMAIN:-ap.alshowto.com}"
CLOUD_INIT_IP="${CLOUD_INIT_IP:-192.168.0.253/23}"
CLOUD_INIT_GATEWAY="${CLOUD_INIT_GATEWAY:-192.168.0.1}"
CLOUD_INIT_DNS="${CLOUD_INIT_DNS_PRIMARY:-192.168.0.250}"

# SSH configuration
SSH_KEY_PATH="${SSH_KEY_PATH:-~/.ssh/id_rsa.pub}"
TIMEZONE="UTC"

# Debian cloud image
DEBIAN_IMAGE_URL="https://cloud.debian.org/images/cloud/bookworm/latest/debian-12-generic-amd64.qcow2"
DEBIAN_IMAGE_FILE="debian-12-generic-amd64.qcow2"

header() {
    echo ""
    echo -e "${BLUE}╔════════════════════════════════════════════════════════════╗${NC}"
    echo -e "${BLUE}║${NC} $1"
    echo -e "${BLUE}╚════════════════════════════════════════════════════════════╝${NC}"
    echo ""
}

header "Debian 12 Cloud Image - Builder VM Deployment"

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
echo "  Storage:         $VM_STORAGE"
echo "  IP/Subnet:       $CLOUD_INIT_IP"
echo "  Gateway:         $CLOUD_INIT_GATEWAY"
echo "  User:            $CLOUD_INIT_USER"
echo "  SSH Key:         $SSH_KEY_PATH"
echo ""

read -p "Continue with deployment? [y/N]: " confirm
if [[ ! $confirm =~ ^[Yy]$ ]]; then
    error "Deployment cancelled"
fi

# Create deployment script for Proxmox
cat > /tmp/builder-cloud-deploy.sh << 'DEPLOY_SCRIPT'
#!/bin/bash
set -e

# Environment variables (passed from calling script)
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
CLOUD_INIT_DOMAIN=${11}
CLOUD_INIT_IP=${12}
CLOUD_INIT_GATEWAY=${13}
CLOUD_INIT_DNS=${14}
SSH_PUB_KEY=${15}
TIMEZONE=${16}

DEBIAN_IMAGE_URL="https://cloud.debian.org/images/cloud/bookworm/latest/debian-12-generic-amd64.qcow2"
DEBIAN_IMAGE_FILE="debian-12-generic-amd64.qcow2"
TEMP_DIR=$(mktemp -d)
cd "$TEMP_DIR"

echo "════════════════════════════════════════════════════════════"
echo "  Debian 12 Cloud Image Deployment"
echo "════════════════════════════════════════════════════════════"
echo ""

# Step 1: Clean up previous attempts
echo "[1/10] Cleaning up previous VM (if exists)..."
qm destroy $VM_ID 2>/dev/null || true
rm -f "$DEBIAN_IMAGE_FILE"
echo "[✓] Cleanup complete"

# Step 2: Install required tools
echo "[2/10] Installing libguestfs-tools..."
apt-get update -qq
apt-get install -y -qq libguestfs-tools > /dev/null 2>&1
echo "[✓] Tools installed"

# Step 3: Download Debian cloud image
echo "[3/10] Downloading Debian 12 cloud image (may take a minute)..."
wget -q "$DEBIAN_IMAGE_URL" -O "$DEBIAN_IMAGE_FILE"
echo "[✓] Image downloaded"

# Step 4: Customize image with virt-customize
echo "[4/10] Customizing image (installing qemu-guest-agent, etc.)..."
virt-customize -a "$DEBIAN_IMAGE_FILE" \
  --install qemu-guest-agent \
  --truncate /etc/machine-id \
  --timezone "$TIMEZONE" \
  --append-line '/etc/sysctl.d/99-custom.conf:net.bridge.bridge-nf-call-iptables=1' \
  --append-line '/etc/sysctl.d/99-custom.conf:net.bridge.bridge-nf-call-ip6tables=1' \
  > /dev/null 2>&1
echo "[✓] Image customized"

# Step 5: Create VM
echo "[5/10] Creating VM $VM_ID ($VM_NAME)..."
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
echo "[6/10] Importing disk image to $VM_STORAGE..."
qm importdisk $VM_ID "$DEBIAN_IMAGE_FILE" "$VM_STORAGE" -format qcow2 > /dev/null 2>&1
echo "[✓] Disk imported"

# Step 7: Configure storage and boot
echo "[7/10] Configuring storage and boot..."
qm set $VM_ID \
  --scsihw virtio-scsi-pci \
  --scsi0 "$VM_STORAGE:vm-$VM_ID-disk-0" \
  --ide2 "$VM_STORAGE:cloudinit" \
  --boot c \
  --bootdisk scsi0 \
  --serial0 socket \
  --vga serial0
echo "[✓] Storage configured"

# Step 8: Resize disk
echo "[8/10] Resizing disk to ${VM_DISK_SIZE}GB..."
qm resize $VM_ID scsi0 +$((VM_DISK_SIZE - 2))G > /dev/null 2>&1
echo "[✓] Disk resized"

# Step 9: Configure cloud-init network and access
echo "[9/10] Configuring cloud-init..."
# Set static IP or DHCP
if [[ "$CLOUD_INIT_IP" == "dhcp" ]]; then
    qm set $VM_ID --ipconfig0 ip=dhcp
else
    qm set $VM_ID --ipconfig0 "ip=$CLOUD_INIT_IP,gw=$CLOUD_INIT_GATEWAY"
fi

# Password handled by cloud-init (SSH key-based auth preferred)
echo "[✓] Cloud-init configured"

# Step 10: Convert to template or ready state
echo "[10/10] Finalizing VM..."
echo "Cloud-init configuration:"
qm cloudinit dump $VM_ID user 2>/dev/null | head -20

# Clean up
cd /
rm -rf "$TEMP_DIR"

echo ""
echo "════════════════════════════════════════════════════════════"
echo "  ✓ Builder VM Ready!"
echo "════════════════════════════════════════════════════════════"
echo ""
echo "VM Details:"
echo "  ID:        $VM_ID"
echo "  Name:      $VM_NAME"
echo "  Cores:     $VM_CORES"
echo "  Memory:    ${VM_MEMORY}MB"
echo "  Storage:   $VM_STORAGE"
echo "  IP:        $CLOUD_INIT_IP"
echo "  Gateway:   $CLOUD_INIT_GATEWAY"
echo "  SSH User:  $CLOUD_INIT_USER"
echo ""
echo "Next Steps:"
echo "  1. Start VM: qm start $VM_ID"
echo "  2. Wait for cloud-init (check logs): qm cloudinit dump $VM_ID user"
echo "  3. SSH access: ssh $CLOUD_INIT_USER@${CLOUD_INIT_IP%/*}"
echo ""

DEPLOY_SCRIPT

chmod +x /tmp/builder-cloud-deploy.sh

# Upload and execute on Proxmox
log "Uploading deployment script to $PROXMOX_HOST..."
sshpass -p "$PROXMOX_PASSWORD" scp -o StrictHostKeyChecking=no \
  /tmp/builder-cloud-deploy.sh "root@$PROXMOX_HOST:/tmp/" 2>/dev/null

log "Executing deployment on $PROXMOX_HOST..."
SSH_PUB_KEY=$(cat "$SSH_KEY_PATH")

sshpass -p "$PROXMOX_PASSWORD" ssh -o StrictHostKeyChecking=no "root@$PROXMOX_HOST" \
  "bash /tmp/builder-cloud-deploy.sh $VM_ID '$VM_NAME' $VM_CORES $VM_MEMORY '$VM_STORAGE' '$VM_BRIDGE' '$VM_MAC' $VM_DISK_SIZE '$CLOUD_INIT_USER' '$CLOUD_INIT_HOSTNAME' '$CLOUD_INIT_DOMAIN' '$CLOUD_INIT_IP' '$CLOUD_INIT_GATEWAY' '$CLOUD_INIT_DNS' '$SSH_PUB_KEY' '$TIMEZONE'"

echo ""
header "✓ Deployment Complete!"

echo "Builder VM has been deployed!"
echo ""
echo "To start the VM and test:"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
echo "1. Start the VM:"
echo "   sshpass -p '$PROXMOX_PASSWORD' ssh -o StrictHostKeyChecking=no root@$PROXMOX_HOST 'qm start $VM_ID'"
echo ""
echo "2. Wait 30-60 seconds for cloud-init to complete, then:"
echo "   ping ${CLOUD_INIT_IP%/*}"
echo "   ssh $CLOUD_INIT_USER@${CLOUD_INIT_IP%/*}"
echo ""
echo "3. Once logged in, test Docker and clone dnsmasq-ui:"
echo "   cd /opt"
echo "   sudo git clone https://github.com/alpauna/dnsmasq-ui.git"
echo "   sudo chown -R $CLOUD_INIT_USER:$CLOUD_INIT_USER /opt/dnsmasq-ui"
echo "   cd /opt/dnsmasq-ui/docker"
echo "   ./build-test-cluster.sh"
echo ""
echo "4. Test DNS queries:"
echo "   dig @172.20.0.250 example.com"
echo ""

# Auto-start option
read -p "Start VM now? [y/N]: " start_now
if [[ $start_now =~ ^[Yy]$ ]]; then
    log "Starting VM $VM_ID..."
    sshpass -p "$PROXMOX_PASSWORD" ssh -o StrictHostKeyChecking=no root@$PROXMOX_HOST \
      "qm start $VM_ID" 2>/dev/null
    success "VM started"
    echo ""
    echo "Waiting for cloud-init to complete (30-60 seconds)..."
    echo "You can check progress with:"
    echo "  ssh debian@${CLOUD_INIT_IP%/*} 'cloud-init status'"
    echo ""
fi
