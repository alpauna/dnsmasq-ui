#!/bin/bash
# Deploy builder VM on Proxmox with cloud-init
# Run this script ON the Proxmox host

set -e

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

log() {
    echo -e "${BLUE}[$(date '+%H:%M:%S')]${NC} $*"
}

success() {
    echo -e "${GREEN}✓ $*${NC}"
}

error() {
    echo -e "${RED}✗ $*${NC}"
    exit 1
}

warning() {
    echo -e "${YELLOW}⚠ $*${NC}"
}

header() {
    echo ""
    echo -e "${BLUE}╔════════════════════════════════════════════════════════════╗${NC}"
    echo -e "${BLUE}║${NC} $1"
    echo -e "${BLUE}╚════════════════════════════════════════════════════════════╝${NC}"
    echo ""
}

# Configuration
VM_ID="${1:-100}"
VM_NAME="builder"
NODE="${2:-$(hostname)}"
STORAGE="local"
STORAGE_LVM="local-lvm"
DISK_SIZE="30"
MEMORY="4096"
CORES="4"
MACHINE="pc"
BIOS="seabios"
MAC="bc:24:11:65:e1:01"
BRIDGE="vmbr0"

echo ""
header "Proxmox Builder VM Deployment"
echo "VM ID:           $VM_ID"
echo "VM Name:         $VM_NAME"
echo "Node:            $NODE"
echo "Memory:          $MEMORY MB"
echo "Cores:           $CORES"
echo "Disk Size:       $DISK_SIZE GB"
echo "MAC Address:     $MAC"
echo ""

# Check if Ubuntu ISO exists
UBUNTU_ISO="/var/lib/vz/template/iso/ubuntu-22.04-live-server-amd64.iso"
if [ ! -f "$UBUNTU_ISO" ]; then
    warning "Ubuntu 22.04 ISO not found at $UBUNTU_ISO"
    echo ""
    echo "You need to download Ubuntu 22.04 LTS Server ISO to Proxmox first:"
    echo "  wget https://releases.ubuntu.com/22.04/ubuntu-22.04-live-server-amd64.iso"
    echo "  mv ubuntu-22.04-live-server-amd64.iso /var/lib/vz/template/iso/"
    echo ""
    read -p "Continue anyway? [y/N]: " continue
    if [[ ! $continue =~ ^[Yy]$ ]]; then
        error "ISO required to continue"
    fi
fi

# Check if VM already exists
if qm list | grep -q "^ *$VM_ID "; then
    error "VM ID $VM_ID already exists. Please use a different ID or delete the existing VM."
fi

log "Creating VM $VM_ID ($VM_NAME)..."

# Create VM
qm create $VM_ID \
    --name "$VM_NAME" \
    --node "$NODE" \
    --machine "$MACHINE" \
    --bios "$BIOS" \
    --cores "$CORES" \
    --memory "$MEMORY" \
    --vga std \
    --serial0 socket \
    --parallel0 none

success "VM created"

# Add hard disk
log "Adding hard disk (${DISK_SIZE}GB)..."
qm set $VM_ID \
    --scsi0 "$STORAGE_LVM:$DISK_SIZE,discard=1,ssd=1"

success "Disk added"

# Add network interface
log "Adding network interface..."
qm set $VM_ID \
    --net0 "virtio=$MAC,bridge=$BRIDGE"

success "Network interface added"

# Add cloud-init support (if available in your Proxmox version)
log "Configuring cloud-init support..."
qm set $VM_ID \
    --ide2 "$STORAGE:cloudinit" \
    --boot "order=scsi0;ide2" \
    --agent 1

success "Cloud-init configured"

# Set cloud-init user data and network config
log "Setting cloud-init user-data and network config..."

# Create cloud-init snippets directory if it doesn't exist
mkdir -p /var/lib/vz/snippets

# Copy cloud-init files to Proxmox
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [ ! -f "$SCRIPT_DIR/cloud-init/builder/user-data.yml" ]; then
    error "user-data.yml not found at $SCRIPT_DIR/cloud-init/builder/"
fi

if [ ! -f "$SCRIPT_DIR/cloud-init/builder/network-config.yml" ]; then
    error "network-config.yml not found at $SCRIPT_DIR/cloud-init/builder/"
fi

log "Copying cloud-init files..."
cp "$SCRIPT_DIR/cloud-init/builder/user-data.yml" /var/lib/vz/snippets/builder-user-data.yml
cp "$SCRIPT_DIR/cloud-init/builder/network-config.yml" /var/lib/vz/snippets/builder-network-config.yml
success "Cloud-init files copied"

# Set cloud-init config in VM
log "Linking cloud-init files to VM..."
qm set $VM_ID \
    -cicustom "local:snippets/builder-user-data.yml,local:snippets/builder-network-config.yml"

success "Cloud-init files linked"

echo ""
header "VM Configuration Complete"
echo ""
echo "Next steps:"
echo ""
echo "1. Download Ubuntu 22.04 LTS Server ISO (if not already downloaded):"
echo "   wget https://releases.ubuntu.com/22.04/ubuntu-22.04-live-server-amd64.iso"
echo "   mv ubuntu-22.04-live-server-amd64.iso /var/lib/vz/template/iso/"
echo ""
echo "2. Attach the ISO to the VM:"
echo "   qm set $VM_ID --ide2 $STORAGE:iso/ubuntu-22.04-live-server-amd64.iso,media=cdrom"
echo ""
echo "3. Start the VM:"
echo "   qm start $VM_ID"
echo ""
echo "4. Open console and follow Ubuntu installation:"
echo "   qm terminal $VM_ID"
echo ""
echo "5. During installation:"
echo "   - Select English for language"
echo "   - Continue with defaults"
echo "   - Accept Ubuntu installation"
echo "   - Enable cloud-init: Yes"
echo "   - Set hostname: builder"
echo "   - Set username: debian"
echo "   - Skip password (we'll use SSH key)"
echo "   - Import SSH identity: Yes, paste your public key"
echo ""
echo "6. After installation completes:"
echo "   - Click 'Reboot Now'"
echo "   - Cloud-init will configure the system (3-5 minutes)"
echo ""
echo "7. Once cloud-init finishes, test from your machine:"
echo "   ping 192.168.0.253"
echo "   ssh debian@192.168.0.253"
echo ""
echo "8. Clone the repository on the builder VM:"
echo "   ssh debian@192.168.0.253"
echo "   cd /opt"
echo "   sudo git clone https://github.com/alpauna/dnsmasq-ui.git"
echo "   sudo chown -R debian:debian /opt/dnsmasq-ui"
echo ""
echo "9. Test Docker deployment:"
echo "   cd /opt/dnsmasq-ui/docker"
echo "   ./build-test-cluster.sh"
echo ""
