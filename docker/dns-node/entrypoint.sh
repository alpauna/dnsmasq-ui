#!/bin/bash
set -e

# DNS Node Entrypoint Script
# Configures and starts dnsmasq, keepalived, and sshd

# ============================================================================
# Configuration
# ============================================================================

KEEPALIVED_STATE=${KEEPALIVED_STATE:-BACKUP}
KEEPALIVED_PRIORITY=${KEEPALIVED_PRIORITY:-100}
KEEPALIVED_VIP=${KEEPALIVED_VIP:-192.168.0.252}
KEEPALIVED_INTERFACE=${KEEPALIVED_INTERFACE:-eth0}
KEEPALIVED_VRID=${KEEPALIVED_VRID:-51}
NODE_HOSTNAME=${NODE_HOSTNAME:-dns-node}

# ============================================================================
# SSH Setup
# ============================================================================

# Generate SSH host key if not present
if [ ! -f /etc/ssh/ssh_host_rsa_key ]; then
    echo "[*] Generating SSH host key..."
    ssh-keygen -A
fi

# Add authorized_keys if provided
if [ -f /tmp/authorized_keys ]; then
    mkdir -p /root/.ssh
    cp /tmp/authorized_keys /root/.ssh/authorized_keys
    chmod 600 /root/.ssh/authorized_keys
    rm /tmp/authorized_keys
fi

# Enable password login for root (for testing)
echo "PermitRootLogin yes" >> /etc/ssh/sshd_config
echo "PasswordAuthentication no" >> /etc/ssh/sshd_config

# ============================================================================
# Keepalived Setup
# ============================================================================

echo "[*] Configuring keepalived..."

cat > /etc/keepalived/keepalived.conf << EOF
global_defs {
  router_id DNS_CLUSTER
  script_user root
}

# Health check for dnsmasq
vrrp_script check_dnsmasq {
  script "systemctl is-active dnsmasq"
  interval 2
  weight -20
  fall 2
  rise 2
}

vrrp_instance DNS_VIP {
  state ${KEEPALIVED_STATE}
  interface ${KEEPALIVED_INTERFACE}
  virtual_router_id ${KEEPALIVED_VRID}
  priority ${KEEPALIVED_PRIORITY}
  advert_int 1
  authentication {
    auth_type PASS
    auth_pass 1111
  }

  virtual_ipaddress {
    ${KEEPALIVED_VIP}/24
  }

  track_processes {
    dnsmasq
  }

  track_script {
    check_dnsmasq
  }
}
EOF

# ============================================================================
# dnsmasq Setup
# ============================================================================

echo "[*] Setting up dnsmasq..."

# Ensure dnsmasq config directory exists
mkdir -p /etc/dnsmasq.d

# Configure dnsmasq to use local records if they exist
if [ ! -f /etc/dnsmasq.conf.orig ]; then
    cp /etc/dnsmasq.conf /etc/dnsmasq.conf.orig
fi

# Add conf-dir if not present
if ! grep -q "^conf-dir=/etc/dnsmasq.d" /etc/dnsmasq.conf; then
    echo "conf-dir=/etc/dnsmasq.d" >> /etc/dnsmasq.conf
fi

# Bind to all interfaces
if ! grep -q "^listen-address=" /etc/dnsmasq.conf; then
    echo "listen-address=0.0.0.0" >> /etc/dnsmasq.conf
fi

# Load DNS records from zones.json if present
if [ -f /etc/dnsmasq-ui/zones.json ]; then
    echo "[*] Processing DNS records from zones.json..."

    # Parse zones.json and generate dnsmasq address records
    # Using simple grep/awk approach since jq may not be available
    python3 << 'PYTHON_EOF'
import json
import os

try:
    with open('/etc/dnsmasq-ui/zones.json', 'r') as f:
        config = json.load(f)

    # Generate dnsmasq configuration from zones
    with open('/etc/dnsmasq.d/zones.conf', 'w') as out:
        if 'zones' in config:
            for zone_name, zone_data in config['zones'].items():
                if 'records' in zone_data:
                    for record in zone_data['records']:
                        domain = record.get('domain', '')
                        record_type = record.get('type', 'A')
                        value = record.get('value', '')

                        if domain and value:
                            if record_type == 'A':
                                out.write(f'address=/{domain}/{value}\n')
                            elif record_type == 'AAAA':
                                out.write(f'address=/{domain}/{value}\n')
                            elif record_type == 'CNAME':
                                out.write(f'cname={domain},{value}\n')

    print("[+] DNS records configured")
except Exception as e:
    print(f"[!] Failed to load zones.json: {e}")
PYTHON_EOF
fi

# ============================================================================
# Firewall Configuration (WireGuard DNS Access Control)
# ============================================================================

WG_FIREWALL_ENABLE=${WG_FIREWALL_ENABLE:-false}
WG_INTERFACE=${WG_INTERFACE:-wg0}

if [ "${WG_FIREWALL_ENABLE}" = "true" ]; then
    echo "[*] Configuring firewall rules (WireGuard DNS access control)..."

    # Function to safely add iptables rule (ignore if already exists)
    add_rule() {
        local chain=$1
        local rule=$2
        if ! iptables -C $chain $rule 2>/dev/null; then
            iptables -A $chain $rule 2>/dev/null || true
        fi
    }

    # Function for IPv6
    add_rule_v6() {
        local chain=$1
        local rule=$2
        if ! ip6tables -C $chain $rule 2>/dev/null; then
            ip6tables -A $chain $rule 2>/dev/null || true
        fi
    }

    # Set default policies to DROP (deny all)
    iptables -P INPUT DROP 2>/dev/null || true
    iptables -P FORWARD DROP 2>/dev/null || true
    iptables -P OUTPUT ACCEPT 2>/dev/null || true

    ip6tables -P INPUT DROP 2>/dev/null || true
    ip6tables -P FORWARD DROP 2>/dev/null || true
    ip6tables -P OUTPUT ACCEPT 2>/dev/null || true

    # IPv4 Rules
    # ==========

    # 1. Allow loopback (required for services)
    add_rule "INPUT" "-i lo -j ACCEPT"

    # 2. Allow SSH on eth0 (management/admin access)
    add_rule "INPUT" "-i eth0 -p tcp --dport 22 -j ACCEPT"

    # 3. Allow keepalived VRRP on eth0 (cluster heartbeat)
    add_rule "INPUT" "-i eth0 -p 112 -j ACCEPT"

    # 4. Allow established/related connections (important for DNS replies)
    add_rule "INPUT" "-m state --state ESTABLISHED,RELATED -j ACCEPT"

    # 5. Allow DNS (TCP/UDP 53) on WireGuard interface ONLY (main feature)
    add_rule "INPUT" "-i ${WG_INTERFACE} -p tcp --dport 53 -j ACCEPT"
    add_rule "INPUT" "-i ${WG_INTERFACE} -p udp --dport 53 -j ACCEPT"

    # IPv6 Rules
    # ==========

    # 1. Allow loopback
    add_rule_v6 "INPUT" "-i lo -j ACCEPT"

    # 2. Allow SSH on eth0
    add_rule_v6 "INPUT" "-i eth0 -p tcp --dport 22 -j ACCEPT"

    # 3. Allow keepalived VRRP
    add_rule_v6 "INPUT" "-i eth0 -p 112 -j ACCEPT"

    # 4. Allow established/related
    add_rule_v6 "INPUT" "-m state --state ESTABLISHED,RELATED -j ACCEPT"

    # 5. Allow DNS on WireGuard interface
    add_rule_v6 "INPUT" "-i ${WG_INTERFACE} -p tcp --dport 53 -j ACCEPT"
    add_rule_v6 "INPUT" "-i ${WG_INTERFACE} -p udp --dport 53 -j ACCEPT"

    echo "[+] Firewall rules configured:"
    echo "    - DNS (53/TCP,UDP) allowed on ${WG_INTERFACE}"
    echo "    - SSH (22/TCP) allowed on eth0 (management)"
    echo "    - Keepalived (protocol 112) allowed on eth0"
    echo "    - All other traffic blocked (implicit deny)"
else
    echo "[*] Firewall disabled (set WG_FIREWALL_ENABLE=true to enable)"
fi

# ============================================================================
# Start Services
# ============================================================================

echo "[*] Starting services..."

# Start SSH daemon
/usr/sbin/sshd -D &
SSH_PID=$!
echo "[+] SSH started (PID: $SSH_PID)"

# Start dnsmasq (daemon mode since we'll background it)
/usr/sbin/dnsmasq -C /etc/dnsmasq.conf &
DNSMASQ_PID=$!
echo "[+] dnsmasq started (PID: $DNSMASQ_PID)"

# Give dnsmasq time to start
sleep 2

# Start keepalived in foreground (keeps container alive)
echo "[+] Starting keepalived (${KEEPALIVED_STATE}, priority ${KEEPALIVED_PRIORITY})..."
/usr/sbin/keepalived -D -f /etc/keepalived/keepalived.conf --log-facility 0 --dont-fork
