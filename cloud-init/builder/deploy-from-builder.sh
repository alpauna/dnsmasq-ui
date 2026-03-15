#!/bin/bash
# Quick deployment script for builder VM
# Runs Docker test cluster and optionally deploys to real DNS servers

set -e

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
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

header() {
    echo ""
    echo -e "${CYAN}╔════════════════════════════════════════════════════════════╗${NC}"
    echo -e "${CYAN}║${NC} $1"
    echo -e "${CYAN}╚════════════════════════════════════════════════════════════╝${NC}"
    echo ""
}

# Check if running on builder VM
if [ "$(hostname)" != "builder" ]; then
    echo -e "${YELLOW}⚠ Not running on builder VM (hostname is $(hostname))${NC}"
    read -p "Continue anyway? [y/N]: " continue
    if [[ ! $continue =~ ^[Yy]$ ]]; then
        exit 1
    fi
fi

header "dnsmasq-ui Deployment from Builder VM"

# Check if dnsmasq-ui directory exists
if [ ! -d "/opt/dnsmasq-ui" ]; then
    log "Cloning dnsmasq-ui repository..."
    sudo git clone https://github.com/alpauna/dnsmasq-ui.git /opt/dnsmasq-ui
    sudo chown -R debian:debian /opt/dnsmasq-ui
    cd /opt/dnsmasq-ui
    success "Repository cloned"
else
    cd /opt/dnsmasq-ui
    log "Repository already exists, updating..."
    git pull
fi

echo ""
echo "What would you like to do?"
echo "  1. Test Docker local cluster (3 containers on 172.20.0.0/24)"
echo "  2. Deploy to existing DNS servers (Ansible)"
echo "  3. Both (Docker test first, then deploy)"
echo "  4. Generate cloud-init files for VM deployments"
echo ""
read -p "Select option [1]: " OPTION
OPTION=${OPTION:-1}

# Option 1: Docker test
if [ "$OPTION" = "1" ] || [ "$OPTION" = "3" ]; then
    header "Starting Docker Test Cluster"

    if ! command -v docker &> /dev/null; then
        error "Docker not installed. Run: sudo apt-get install docker.io"
    fi

    log "Building DNS node image and starting containers..."
    cd "$SCRIPT_DIR/docker"

    if [ ! -f dns-cluster.yml ]; then
        error "dns-cluster.yml not found. Run ./setup.sh first"
    fi

    ./build-test-cluster.sh

    echo ""
    success "Docker test cluster running!"
    echo ""
    echo "Test the cluster:"
    echo "  docker ps                                  # List containers"
    echo "  dig @172.20.0.250 example.com             # Test DNS"
    echo "  docker logs dns01                         # View logs"
    echo "  docker compose -f dns-cluster.yml down    # Stop"
    echo ""

    if [ "$OPTION" = "1" ]; then
        exit 0
    fi
fi

# Option 2: Deploy to existing servers
if [ "$OPTION" = "2" ] || [ "$OPTION" = "3" ]; then
    header "Deploy to Existing DNS Servers"

    # Check if inventory exists
    if [ ! -f "ansible/inventory.ini" ]; then
        log "No Ansible inventory found. Running setup.sh..."
        ./setup.sh
    fi

    # Show current inventory
    echo "Current inventory:"
    echo ""
    cat ansible/inventory.ini
    echo ""

    read -p "Deploy to these servers? [y/N]: " deploy
    if [[ ! $deploy =~ ^[Yy]$ ]]; then
        log "Deployment cancelled"
        exit 0
    fi

    log "Deploying DNS servers and keepalived..."
    cd ansible
    ansible-playbook -i inventory.ini dnsmasq-setup.yml

    success "DNS server deployment complete!"

    echo ""
    read -p "Also deploy HA UI with GlusterFS? [y/N]: " deploy_ui
    if [[ $deploy_ui =~ ^[Yy]$ ]]; then
        log "Deploying HA UI..."
        ansible-playbook -i inventory.ini dnsmasq-ui-ha.yml
        success "HA UI deployment complete!"
    fi

    echo ""
    echo "Verify deployment:"
    echo "  curl http://192.168.0.250/api/status      # Check status"
    echo "  dig @192.168.0.250 example.com            # Test DNS"
    echo ""
fi

# Option 4: Generate cloud-init
if [ "$OPTION" = "4" ]; then
    header "Generate Cloud-init Files"

    log "Checking if zones.json exists..."
    if [ ! -f "zones.json" ]; then
        error "zones.json not found. Run setup.sh first to generate it."
    fi

    log "Generating cloud-init files..."
    ./generate-cloud-init.sh

    success "Cloud-init files generated in ./cloud-init/"
    echo ""
    echo "Next steps:"
    echo "  1. Create VMs on Proxmox"
    echo "  2. Mount cloud-init files during VM creation"
    echo "  3. Boot VMs"
    echo ""
fi

header "Deployment Complete"
echo "For more information, see:"
echo "  README.md - General information"
echo "  SETUP_GUIDE.md - Interactive setup guide"
echo "  HA_UI_DEPLOYMENT.md - HA deployment details"
echo ""
