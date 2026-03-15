#!/bin/bash
# Deploy keepalived to DNS servers
# Usage: ./deploy-keepalived.sh [dns01|dns02|dns03|all]

set -e

VIP="192.168.0.250"
VIRTUAL_ROUTER_ID="51"
SSH_USER="debian"

# Server configuration
declare -A SERVERS=(
    [dns01]="192.168.0.231:150:MASTER"
    [dns02]="192.168.0.232:140:BACKUP"
    [dns03]="192.168.0.233:130:BACKUP"
)

declare -A INTERFACES=(
    [dns01]="eth0"
    [dns02]="eth0"
    [dns03]="eth0"
)

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"
}

error() {
    echo "[ERROR] $*" >&2
    exit 1
}

usage() {
    echo "Usage: $0 [dns01|dns02|dns03|all]"
    echo ""
    echo "Deploy keepalived configuration to DNS servers"
    echo ""
    echo "Options:"
    echo "  dns01    Deploy to dns01 (MASTER, priority 150)"
    echo "  dns02    Deploy to dns02 (BACKUP, priority 140)"
    echo "  dns03    Deploy to dns03 (BACKUP, priority 130)"
    echo "  all      Deploy to all servers (default)"
    exit 0
}

install_keepalived() {
    local server=$1
    local ip=$(echo ${SERVERS[$server]} | cut -d: -f1)

    log "Installing keepalived on $server ($ip)..."
    ssh ${SSH_USER}@${ip} "sudo apt-get update && sudo apt-get install -y keepalived iproute2" || error "Failed to install keepalived on $server"
    log "✓ keepalived installed on $server"
}

configure_keepalived() {
    local server=$1
    local ip=$(echo ${SERVERS[$server]} | cut -d: -f1)
    local priority=$(echo ${SERVERS[$server]} | cut -d: -f2)
    local state=$(echo ${SERVERS[$server]} | cut -d: -f3)
    local interface=${INTERFACES[$server]}

    log "Configuring keepalived on $server (state=$state, priority=$priority)..."

    # Create keepalived config
    cat > /tmp/keepalived-${server}.conf << EOF
global_defs {
  router_id DNS_CLUSTER
  script_user root
}

vrrp_instance DNS_VIP {
  state ${state}
  interface ${interface}
  virtual_router_id ${VIRTUAL_ROUTER_ID}
  priority ${priority}
  advert_int 1

  virtual_ipaddress {
    ${VIP}/24
  }

  track_processes {
    dnsmasq
  }
}
EOF

    # Copy config to server
    scp /tmp/keepalived-${server}.conf ${SSH_USER}@${ip}:/tmp/keepalived.conf || error "Failed to copy config to $server"
    ssh ${SSH_USER}@${ip} "sudo mv /tmp/keepalived.conf /etc/keepalived/keepalived.conf" || error "Failed to move config on $server"
    ssh ${SSH_USER}@${ip} "sudo chown root:root /etc/keepalived/keepalived.conf && sudo chmod 644 /etc/keepalived/keepalived.conf" || error "Failed to set permissions on $server"

    log "✓ keepalived configured on $server"
}

enable_keepalived() {
    local server=$1
    local ip=$(echo ${SERVERS[$server]} | cut -d: -f1)

    log "Enabling and starting keepalived on $server..."
    ssh ${SSH_USER}@${ip} "sudo systemctl enable keepalived && sudo systemctl restart keepalived" || error "Failed to start keepalived on $server"

    # Verify status
    status=$(ssh ${SSH_USER}@${ip} "sudo systemctl is-active keepalived" 2>/dev/null || echo "inactive")
    if [ "$status" = "active" ]; then
        log "✓ keepalived running on $server"
    else
        error "keepalived not running on $server (status: $status)"
    fi
}

verify_vip() {
    local server=$1
    local ip=$(echo ${SERVERS[$server]} | cut -d: -f1)

    log "Verifying VIP assignment on $server..."
    has_vip=$(ssh ${SSH_USER}@${ip} "ip addr show | grep -q ${VIP}" && echo "yes" || echo "no")

    if [ "$has_vip" = "yes" ]; then
        log "✓ VIP ${VIP} is assigned to $server"
    else
        log "⚠ VIP ${VIP} is NOT assigned to $server (expected for BACKUP servers)"
    fi
}

deploy_server() {
    local server=$1

    log "===== Deploying keepalived to $server ====="
    install_keepalived "$server"
    configure_keepalived "$server"
    enable_keepalived "$server"
    verify_vip "$server"
    log "===== $server deployment complete ====="
    echo ""
}

# Parse arguments
SERVERS_TO_DEPLOY=()
if [ $# -eq 0 ]; then
    SERVERS_TO_DEPLOY=(dns01 dns02 dns03)
elif [ "$1" = "all" ]; then
    SERVERS_TO_DEPLOY=(dns01 dns02 dns03)
elif [ "$1" = "-h" ] || [ "$1" = "--help" ]; then
    usage
elif [[ "${!SERVERS[@]}" =~ "$1" ]]; then
    SERVERS_TO_DEPLOY=("$1")
else
    error "Unknown server: $1"
fi

# Verify SSH connectivity
log "Verifying SSH connectivity..."
for server in "${SERVERS_TO_DEPLOY[@]}"; do
    ip=$(echo ${SERVERS[$server]} | cut -d: -f1)
    if ! ssh -o ConnectTimeout=5 ${SSH_USER}@${ip} "echo OK" > /dev/null 2>&1; then
        error "Cannot connect to $server ($ip)"
    fi
done
log "✓ SSH connectivity verified"
echo ""

# Deploy to each server
for server in "${SERVERS_TO_DEPLOY[@]}"; do
    deploy_server "$server"
done

# Final verification
log "===== Final Cluster Status ====="
for server in dns01 dns02 dns03; do
    ip=$(echo ${SERVERS[$server]} | cut -d: -f1)
    status=$(ssh ${SSH_USER}@${ip} "sudo systemctl is-active keepalived" 2>/dev/null || echo "inactive")
    priority=$(echo ${SERVERS[$server]} | cut -d: -f2)
    state=$(echo ${SERVERS[$server]} | cut -d: -f3)
    echo "$server ($ip): $status [$state, priority $priority]"
done

log "===== All servers deployed successfully ====="
