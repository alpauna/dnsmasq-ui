#!/bin/bash
# Generate cloud-init files for each DNS server
# Reads from zones.json and creates per-server cloud-init user-data and network config

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

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

# Check if zones.json exists
if [ ! -f "$SCRIPT_DIR/zones.json" ]; then
    error "zones.json not found. Run setup.sh first to generate configuration."
fi

# Check if ansible/inventory.ini exists
if [ ! -f "$SCRIPT_DIR/ansible/inventory.ini" ]; then
    error "ansible/inventory.ini not found. Run setup.sh first."
fi

log "Generating cloud-init files from zones.json..."

# Create cloud-init directory
mkdir -p "$SCRIPT_DIR/cloud-init"

# Read SSH public key if available
SSH_PUBLIC_KEY=""
if [ -f ~/.ssh/id_rsa.pub ]; then
    SSH_PUBLIC_KEY=$(cat ~/.ssh/id_rsa.pub)
else
    warning "SSH public key not found at ~/.ssh/id_rsa.pub"
    SSH_PUBLIC_KEY="ssh-rsa AAAA... (insert your public key here)"
fi

# Extract server names and IPs from inventory.ini
declare -A SERVERS
declare -A SERVER_IPS
declare -A MACS

while IFS= read -r line; do
    if [[ $line =~ ^(dns[0-9]+)[[:space:]]+ansible_host=([0-9.]+) ]]; then
        SERVER_NAME="${BASH_REMATCH[1]}"
        SERVER_IP="${BASH_REMATCH[2]}"
        SERVERS["$SERVER_NAME"]="$SERVER_IP"
        SERVER_IPS["$SERVER_IP"]="$SERVER_NAME"

        # Check for MAC address comment (if DHCP was used)
        if [[ $line =~ mac_address=([0-9a-fA-F:]+) ]]; then
            MACS["$SERVER_NAME"]="${BASH_REMATCH[1]}"
        fi
    fi
done < "$SCRIPT_DIR/ansible/inventory.ini"

# Detect network type and gateway from zones.json
GATEWAY=$(python3 -c "import json; print(json.load(open('$SCRIPT_DIR/zones.json')).get('global', {}).get('gateway', '192.168.0.1'))" 2>/dev/null || echo "192.168.0.1")

# Generate cloud-init for each server
for SERVER_NAME in "${!SERVERS[@]}"; do
    SERVER_IP="${SERVERS[$SERVER_NAME]}"
    SERVER_DIR="$SCRIPT_DIR/cloud-init/$SERVER_NAME"
    mkdir -p "$SERVER_DIR"

    log "Generating cloud-init for $SERVER_NAME ($SERVER_IP)..."

    # Generate user-data.yml
    USER_DATA_FILE="$SERVER_DIR/user-data.yml"
    cat "$SCRIPT_DIR/cloud-init/user-data.template" > "$USER_DATA_FILE"

    # Replace placeholders
    sed -i "s|__HOSTNAME__|$SERVER_NAME|g" "$USER_DATA_FILE"
    sed -i "s|__SSH_PUBLIC_KEY__|$SSH_PUBLIC_KEY|g" "$USER_DATA_FILE"

    success "Generated: $USER_DATA_FILE"

    # Generate network-config.yml
    NETWORK_CONFIG_FILE="$SERVER_DIR/network-config.yml"

    if [ -n "${MACS[$SERVER_NAME]}" ]; then
        # DHCP mode (has MAC address)
        cat "$SCRIPT_DIR/cloud-init/network-config-dhcp.template" > "$NETWORK_CONFIG_FILE"
        sed -i "s|__MAC_ADDRESS__|${MACS[$SERVER_NAME]}|g" "$NETWORK_CONFIG_FILE"

        # Store MAC in a separate file for reference
        echo "${MACS[$SERVER_NAME]}" > "$SERVER_DIR/mac-address.txt"
        success "Generated: $NETWORK_CONFIG_FILE (DHCP with MAC)"
    else
        # Static mode
        cat "$SCRIPT_DIR/cloud-init/network-config-static.template" > "$NETWORK_CONFIG_FILE"
        sed -i "s|__IP_ADDRESS__|$SERVER_IP|g" "$NETWORK_CONFIG_FILE"
        sed -i "s|__GATEWAY__|$GATEWAY|g" "$NETWORK_CONFIG_FILE"
        success "Generated: $NETWORK_CONFIG_FILE (Static IP)"
    fi
done

echo ""
echo "==================================================================="
echo "Cloud-init Generation Complete"
echo "==================================================================="
echo ""
echo "Generated files are located in: $SCRIPT_DIR/cloud-init/"
echo ""
echo "For each server:"
echo "  user-data.yml       - SSH keys, packages, runcmd"
echo "  network-config.yml  - Network configuration (static or DHCP)"
echo "  [mac-address.txt]   - MAC address (if DHCP mode)"
echo ""
echo "To use with Proxmox or other cloud providers:"
echo "  1. Create a new VM"
echo "  2. Mount cloud-init user-data: cloud-init/dns01/user-data.yml"
echo "  3. Mount cloud-init network: cloud-init/dns01/network-config.yml"
echo "  4. Boot the VM"
echo ""
