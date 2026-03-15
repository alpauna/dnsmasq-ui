#!/bin/bash
# dnsmasq-ui Setup Script
# Interactive configuration for DNS cluster deployment

set -e

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m'

# Script directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

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

info() {
    echo -e "${CYAN}ℹ $*${NC}"
}

header() {
    echo ""
    echo -e "${CYAN}╔════════════════════════════════════════════════════════════╗${NC}"
    echo -e "${CYAN}║${NC} $1"
    echo -e "${CYAN}╚════════════════════════════════════════════════════════════╝${NC}"
    echo ""
}

# Parse IP range into array
parse_ip_range() {
    local input=$1
    local ips=()

    # Check if it's a range (e.g., 192.168.0.231-233)
    if [[ $input =~ ^([0-9.]+)-([0-9]+)$ ]]; then
        local base="${BASH_REMATCH[1]}"
        local end_octet="${BASH_REMATCH[2]}"
        local base_prefix=$(echo $base | rev | cut -d. -f2- | rev)
        local start_octet=$(echo $base | rev | cut -d. -f1 | rev)

        for ((i=start_octet; i<=end_octet; i++)); do
            ips+=("${base_prefix}.${i}")
        done
    # Check if it's a comma-separated list
    elif [[ $input == *","* ]]; then
        IFS=',' read -ra ips <<< "$input"
        for i in "${!ips[@]}"; do
            ips[$i]=$(echo "${ips[$i]}" | xargs)  # trim whitespace
        done
    # Single IP
    else
        ips=("$input")
    fi

    printf '%s\n' "${ips[@]}"
}

# Validate IP address
validate_ip() {
    local ip=$1
    if [[ $ip =~ ^[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}$ ]]; then
        return 0
    else
        return 1
    fi
}

# Test SSH connectivity
test_ssh() {
    local ip=$1
    local user=$2
    if timeout 5 ssh -o ConnectTimeout=3 "$user@$ip" "echo OK" > /dev/null 2>&1; then
        return 0
    else
        return 1
    fi
}

# Generate random MAC address (QEMU/KVM format: 52:54:00:XX:XX:XX)
generate_mac() {
    printf "52:54:00:%02x:%02x:%02x" $((RANDOM % 256)) $((RANDOM % 256)) $((RANDOM % 256))
}

# Generate Ansible inventory
generate_inventory() {
    local output_file="$SCRIPT_DIR/ansible/inventory.ini"
    local ssh_user=$1
    shift
    local ips=("$@")

    log "Generating Ansible inventory..."

    cat > "$output_file" << EOF
[dns_servers]
EOF

    for i in "${!ips[@]}"; do
        local dns_num=$((i + 1))
        local server_name="dns$(printf "%02d" $dns_num)"
        echo "${server_name} ansible_host=${ips[$i]} ansible_user=${ssh_user} ansible_become=yes" >> "$output_file"
    done

    cat >> "$output_file" << EOF

[dns_servers:vars]
ansible_python_interpreter=/usr/bin/python3
ansible_ssh_private_key_file=~/.ssh/id_rsa
EOF

    success "Inventory generated at $output_file"
}

# Generate Ansible keepalived config with dynamic priorities
generate_keepalived_playbook() {
    local output_file="$SCRIPT_DIR/ansible/dnsmasq-setup.yml"
    local num_servers=$1
    local keepalive_vip=$2
    shift 2
    local ips=("$@")

    log "Generating Ansible playbook with keepalived for $num_servers servers..."

    cat > "$output_file" <<EOF
---
- name: Setup dnsmasq DNS servers with monitoring
  hosts: dns_servers
  become: yes
  gather_facts: yes

  vars:
    dnsmasq_config_dir: /etc/dnsmasq.d
    dnsmasq_records_file: "{{ dnsmasq_config_dir }}/local-records.conf"
    monitoring_script: /usr/local/bin/dnsmasq-monitor.sh
    keepalive_vip: $keepalive_vip

  tasks:
    - name: Install dnsmasq, keepalived, and dependencies
      apt:
        name:
          - dnsmasq
          - dnsutils
          - net-tools
          - systemd
          - keepalived
          - iproute2
        state: present
        update_cache: yes

    - name: Stop systemd-resolved
      systemd:
        name: systemd-resolved
        state: stopped
        enabled: no

    - name: Set keepalived priority based on hostname
      set_fact:
        keepalived_state: "{{ 'MASTER' if inventory_hostname == groups['dns_servers'][0] else 'BACKUP' }}"
        keepalived_priority: "{{ 150 - (groups['dns_servers'].index(inventory_hostname) * 10) }}"

    - name: Create keepalived configuration
      copy:
        content: |
          global_defs {
            router_id DNS_CLUSTER
            script_user root
          }

          # Health check for dnsmasq-ui (port 5000)
          vrrp_script check_ui {
            script "curl -sf http://localhost:5000/api/status > /dev/null"
            interval 10
            weight -20
            fall 2
            rise 2
          }

          vrrp_instance DNS_VIP {
            state {{ keepalived_state }}
            interface eth0
            virtual_router_id 51
            priority {{ keepalived_priority }}
            advert_int 1

            virtual_ipaddress {
              {{ keepalive_vip }}/24
            }

            track_processes {
              dnsmasq
            }

            track_script {
              check_ui
            }
          }
        dest: /etc/keepalived/keepalived.conf
        owner: root
        group: root
        mode: '0644'
      notify: restart keepalived

    - name: Enable and start keepalived
      systemd:
        name: keepalived
        enabled: yes
        state: started

    - name: Create dnsmasq records file
      copy:
        content: |
          # Local DNS Records for ad.alshowto.com
          # Generated by Ansible

          address=/10g-sw01.ad.alshowto.com/2604:7a00:ea40:5630:5ea6:e6ff:fe27:417c
          address=/10g-sw02.ad.alshowto.com/2604:7a00:ea40:5630:56af:97ff:fe8f:c7a7
          address=/dns01.ad.alshowto.com/192.168.0.231
          address=/dns02.ad.alshowto.com/192.168.0.232
          address=/dns03.ad.alshowto.com/192.168.0.233
          address=/mfc-printer.ad.alshowto.com/192.168.0.70
          address=/middle-01.ad.alshowto.com/192.168.0.250

          cname=esphome.ad.alshowto.com,middle-01.ad.alshowto.com
          cname=frigate.ad.alshowto.com,middle-01.ad.alshowto.com
          cname=ha-tainer.ad.alshowto.com,middle-01.ad.alshowto.com
          cname=ha.ad.alshowto.com,middle-01.ad.alshowto.com
          cname=ma.ad.alshowto.com,middle-01.ad.alshowto.com
          cname=music.ad.alshowto.com,middle-01.ad.alshowto.com
          cname=nginx-proxy.ad.alshowto.com,middle-01.ad.alshowto.com
          cname=nginx-proxy.cld.alshowto.com,middle-01.ad.alshowto.com
          cname=piehole.ad.alshowto.com,middle-01.ad.alshowto.com
          cname=portainer.ad.alshowto.com,middle-01.ad.alshowto.com
          cname=proxmox.ad.alshowto.com,middle-01.ad.alshowto.com
          cname=rtr.ad.alshowto.com,middle-01.ad.alshowto.com
          cname=wordpress-dbadmin.ad.alshowto.com,middle-01.ad.alshowto.com

          # Upstream DNS servers
          server=1.1.1.1
          server=8.8.8.8
        dest: "{{ dnsmasq_records_file }}"
        owner: root
        group: root
        mode: '0644'
      notify: restart dnsmasq

    - name: Create dnsmasq main config
      lineinfile:
        path: /etc/dnsmasq.conf
        regexp: "^conf-dir={{ dnsmasq_config_dir }}"
        line: "conf-dir={{ dnsmasq_config_dir }}"
        state: present
      notify: restart dnsmasq

    - name: Enable and start dnsmasq
      systemd:
        name: dnsmasq
        enabled: yes
        state: started

    - name: Create monitoring script
      copy:
        content: |
          #!/bin/bash
          # dnsmasq Health Check Script
          # Monitors dnsmasq service and logs keepalive status

          LOG_FILE="/var/log/dnsmasq-monitor.log"
          STATUS_FILE="/var/run/dnsmasq-status"

          check_dnsmasq() {
              if systemctl is-active --quiet dnsmasq; then
                  echo "online" > $STATUS_FILE
                  return 0
              else
                  echo "offline" > $STATUS_FILE
                  return 1
              fi
          }

          check_dns() {
              # Test DNS resolution
              dig @127.0.0.1 +short walmart.com > /dev/null 2>&1
              return $?
          }

          log_status() {
              TIMESTAMP=$(date '+%Y-%m-%d %H:%M:%S')
              HOSTNAME=$(hostname)
              STATUS=$(cat $STATUS_FILE)
              echo "[$TIMESTAMP] $HOSTNAME - $STATUS" >> $LOG_FILE
          }

          check_dnsmasq && check_dns && STATUS="healthy" || STATUS="degraded"
          log_status
        dest: "{{ monitoring_script }}"
        owner: root
        group: root
        mode: '0755'

    - name: Create keepalive check cron job
      cron:
        name: "dnsmasq health check"
        minute: "*/5"
        job: "{{ monitoring_script }}"
        user: root

    - name: Verify DNS is working
      command: dig @127.0.0.1 +short walmart.com
      register: dns_test
      failed_when: dns_test.stdout == ""

    - name: Display DNS test result
      debug:
        msg: "DNS working: {{ dns_test.stdout }}"

  handlers:
    - name: restart dnsmasq
      systemd:
        name: dnsmasq
        state: restarted

    - name: restart keepalived
      systemd:
        name: keepalived
        state: restarted
EOF

    success "Ansible playbook generated at $output_file"
}

# Update zones.json with new servers
update_zones_config() {
    local zones_file="$SCRIPT_DIR/zones.json"
    local num_servers=$1
    local keepalive_vip=$2
    local gateway_ip=$3
    local subnet_cidr=$4
    shift 4
    local ips=("$@")

    log "Updating zones.json with server configuration..."

    # Use Python to update JSON while preserving structure
    python3 << PYEOF
import json
import sys

zones_file = '$zones_file'
num_servers = $num_servers
ips = [${ips[@]/#/\"}]  # Quote each IP
ipv6s = [${DNS_IPV6S[@]/#/\"}]  # Quote each IPv6

with open(zones_file, 'r') as f:
    config = json.load(f)

# Build servers dict
servers = {}
for i in range(num_servers):
    dns_num = i + 1
    server_name = f"dns{dns_num:02d}"
    servers[server_name] = {
        "ip": ips[i].strip('\"'),
        "hostname": server_name,
        "port": 22,
        "enabled": True
    }
    # Add IPv6 if available and not empty
    if i < len(ipv6s):
        ipv6 = ipv6s[i].strip('\"')
        if ipv6:
            servers[server_name]["ipv6"] = ipv6

# Update servers with new configuration
config['servers'] = servers

# Update global settings
if 'global' not in config:
    config['global'] = {}

config['global']['keepalived_vip'] = '$keepalive_vip'
if '$gateway_ip':
    config['global']['gateway'] = '$gateway_ip'
if '$subnet_cidr':
    config['global']['subnet_cidr'] = '$subnet_cidr'

with open(zones_file, 'w') as f:
    json.dump(config, f, indent=2)

print(f"Updated zones.json with {num_servers} servers")
PYEOF

    success "zones.json updated with $num_servers servers"
}

# Generate Docker Compose for test cluster
generate_docker_compose() {
    local output_file="$SCRIPT_DIR/docker/dns-cluster.yml"
    local num_servers=$1
    local keepalive_vip=$2
    shift 2
    local ips=("$@")

    log "Generating Docker Compose for test cluster..."

    cat > "$output_file" << 'EOF'
version: '3.8'

networks:
  dnsmasq-net:
    driver: bridge
    ipam:
      config:
        - subnet: 172.20.0.0/24

services:
EOF

    for i in "${!ips[@]}"; do
        local dns_num=$((i + 1))
        local server_name="dns$(printf "%02d" $dns_num)"
        local container_ip="172.20.0.$((230 + i))"
        local priority=$((150 - (i * 10)))
        local state="MASTER"
        if [ $i -gt 0 ]; then
            state="BACKUP"
        fi

        cat >> "$output_file" << EOF

  $server_name:
    build:
      context: ./dns-node
      dockerfile: Dockerfile
    container_name: $server_name
    hostname: $server_name
    networks:
      dnsmasq-net:
        ipv4_address: $container_ip
    environment:
      - KEEPALIVED_STATE=$state
      - KEEPALIVED_PRIORITY=$priority
      - KEEPALIVED_VIP=$keepalive_vip
      - KEEPALIVED_INTERFACE=eth0
      - KEEPALIVED_VRID=51
      - NODE_HOSTNAME=$server_name
    cap_add:
      - NET_ADMIN
      - NET_RAW
      - SYS_ADMIN
    volumes:
      - ../zones.json:/etc/dnsmasq-ui/zones.json:ro
      - ./test-keys/id_rsa.pub:/tmp/authorized_keys:ro
    restart: unless-stopped
    stdin_open: true
    tty: true
EOF
    done

    success "Docker Compose generated at $output_file"
}

# Main setup flow
main() {
    header "dnsmasq-ui Interactive Setup"

    echo "This script will configure your DNS cluster with Ansible and keepalived."
    echo ""

    # Get SSH user
    read -p "SSH user for DNS servers [debian]: " SSH_USER
    SSH_USER=${SSH_USER:-debian}
    log "Using SSH user: $SSH_USER"

    # Get deployment target
    header "Deployment Target"
    echo "Which deployment type do you want?"
    echo "  1. Existing servers (SSH-based, Ansible deployment)"
    echo "  2. Docker local test (create containers locally)"
    echo "  3. Proxmox VMs (generate cloud-init files) [future]"
    echo ""
    read -p "Select deployment target [1]: " DEPLOYMENT_TARGET
    DEPLOYMENT_TARGET=${DEPLOYMENT_TARGET:-1}

    # Auto-configure Docker mode
    if [ "$DEPLOYMENT_TARGET" = "2" ]; then
        log "Docker test cluster mode selected"
        DOCKER_MODE=true
        NUM_SERVERS=3
        DNS_IPS=("172.20.0.231" "172.20.0.232" "172.20.0.233")
        KEEPALIVE_VIP="172.20.0.250"
        NETWORK_TYPE="static"
    else
        DOCKER_MODE=false

        # Get number of servers
        read -p "Number of DNS servers [3]: " NUM_SERVERS
        NUM_SERVERS=${NUM_SERVERS:-3}
    fi

    if [ "$DOCKER_MODE" != "true" ]; then
        if ! [[ $NUM_SERVERS =~ ^[0-9]+$ ]] || [ $NUM_SERVERS -lt 1 ]; then
            error "Invalid number of servers. Must be >= 1"
        fi

        log "Configuring for $NUM_SERVERS DNS server(s)"
        echo ""

        # Get network type (static or DHCP)
        header "Network Configuration"
        echo "How should servers be configured?"
        echo "  1. Static IP addresses (you specify each IP)"
        echo "  2. DHCP (we'll generate MAC addresses)"
        echo ""
        read -p "Select network type [1]: " NETWORK_CHOICE
        NETWORK_CHOICE=${NETWORK_CHOICE:-1}

        if [ "$NETWORK_CHOICE" = "2" ]; then
            NETWORK_TYPE="dhcp"
        else
            NETWORK_TYPE="static"
        fi

        if [ "$NETWORK_TYPE" = "dhcp" ]; then
            # Generate MAC addresses
            header "Generating MAC Addresses"
            echo "MAC addresses for DHCP-based deployment:"
            echo ""
            echo "┌────────┬──────────────────────────────┐"
            echo "│ Server │ MAC Address                  │"
            echo "├────────┼──────────────────────────────┤"

            declare -A MACS
            for ((i=0; i<NUM_SERVERS; i++)); do
                local dns_num=$((i + 1))
                local server_name="dns$(printf "%02d" $dns_num)"
                local mac=$(generate_mac)
                MACS["$server_name"]="$mac"
                printf "│ %s     │ %s │\n" "$server_name" "$mac"
            done
            echo "└────────┴──────────────────────────────┘"
            echo ""
            echo "After creating DHCP reservations with these MACs,"
            echo "enter the IPs your DHCP server will assign:"
            echo ""

            # Prompt for reserved IPs
            mapfile -t DNS_IPS < <(for i in $(seq 1 $NUM_SERVERS); do
                read -p "DNS$i IP address: " ip
                echo "$ip"
            done)
        else
            # Static IP mode
            # Get server addresses
            header "DNS Server Addresses"
            echo "Enter DNS server addresses as:"
            echo "  - Single IP: 192.168.0.231"
            echo "  - Range: 192.168.0.231-233 (generates .231, .232, .233)"
            echo "  - List: 192.168.0.231, 192.168.0.232, 192.168.0.233"
            echo ""

            read -p "Enter $NUM_SERVERS server address(es): " SERVER_INPUT

            # Parse and validate addresses
            mapfile -t DNS_IPS < <(parse_ip_range "$SERVER_INPUT")
        fi

        if [ ${#DNS_IPS[@]} -ne $NUM_SERVERS ]; then
            error "Expected $NUM_SERVERS addresses, got ${#DNS_IPS[@]}"
        fi

        # Validate each IP
        log "Validating IP addresses..."
        for ip in "${DNS_IPS[@]}"; do
            if ! validate_ip "$ip"; then
                error "Invalid IP address: $ip"
            fi
            success "Valid: $ip"
        done
        echo ""

        # Optional IPv6 addresses
        header "IPv6 Addresses (Optional)"
        echo "Enter IPv6 addresses for each server (optional - leave blank to skip)."
        echo "Format: 2001:db8::1/64, fe80::1/10, etc."
        echo ""

        declare -a DNS_IPV6S
        for i in "${!DNS_IPS[@]}"; do
            local dns_num=$((i + 1))
            read -p "DNS$dns_num IPv6 address (optional): " ipv6
            if [ -n "$ipv6" ]; then
                # Basic IPv6 validation - just check for colons and hex
                if [[ $ipv6 =~ ^[0-9a-fA-F:]+(/[0-9]+)?$ ]]; then
                    DNS_IPV6S+=("$ipv6")
                    success "Valid IPv6: $ipv6"
                else
                    error "Invalid IPv6 address format: $ipv6"
                fi
            else
                DNS_IPV6S+=("")
            fi
        done
        echo ""

        # Get keepalived VIP
        header "Keepalived Virtual IP (VIP)"
        echo "The VIP is a shared IP address used for failover."
        echo "It will be assigned to the MASTER server and move to a BACKUP if the MASTER fails."
        echo "Both DNS (port 53) and the UI (port 5000) will use this VIP."
        echo ""
        read -p "VIP address [192.168.0.250]: " KEEPALIVE_VIP
        KEEPALIVE_VIP=${KEEPALIVE_VIP:-192.168.0.250}

        if ! validate_ip "$KEEPALIVE_VIP"; then
            error "Invalid VIP address: $KEEPALIVE_VIP"
        fi
        log "Using VIP: $KEEPALIVE_VIP"
        echo ""

        # Get gateway and subnet only for non-Docker deployments
        if [ "$DOCKER_MODE" != "true" ]; then
            # Get gateway address
            header "Network Gateway"
            echo "Enter the default gateway for the network (used in cloud-init configurations)."
            echo ""
            read -p "Gateway address [192.168.0.1]: " GATEWAY_IP
            GATEWAY_IP=${GATEWAY_IP:-192.168.0.1}

            if ! validate_ip "$GATEWAY_IP"; then
                error "Invalid gateway address: $GATEWAY_IP"
            fi
            log "Using gateway: $GATEWAY_IP"
            echo ""

            # Get subnet/CIDR
            header "Network Subnet"
            echo "Enter the subnet CIDR notation (e.g., /24 for 255.255.255.0, /23 for 255.255.254.0)."
            echo "This is used in cloud-init configurations for static IP addresses."
            echo ""
            read -p "Subnet/CIDR [/24]: " SUBNET_CIDR
            SUBNET_CIDR=${SUBNET_CIDR:-/24}

            # Validate CIDR notation
            if ! [[ "$SUBNET_CIDR" =~ ^/[0-9]+$ ]]; then
                error "Invalid subnet notation. Use format like /24, /23, /22, etc."
            fi

            log "Using subnet: $SUBNET_CIDR"
            echo ""
        fi

        # Test SSH connectivity
        header "Testing SSH Connectivity"
        echo "Testing SSH access to servers (this may take a moment)..."
        echo ""

        local all_connected=true
        for i in "${!DNS_IPS[@]}"; do
            local dns_num=$((i + 1))
            local server_name="dns$(printf "%02d" $dns_num)"
            local ip=${DNS_IPS[$i]}

            if test_ssh "$ip" "$SSH_USER"; then
                success "SSH to $server_name ($ip): OK"
            else
                warning "SSH to $server_name ($ip): FAILED"
                all_connected=false
            fi
        done
        echo ""

        if [ "$all_connected" = false ]; then
            warning "Some servers are not accessible via SSH"
            read -p "Continue anyway? [y/N]: " continue_anyway
            if [[ ! $continue_anyway =~ ^[Yy]$ ]]; then
                error "Setup cancelled"
            fi
        fi
    fi

    # Show configuration summary
    header "Configuration Summary"
    echo "SSH User:         $SSH_USER"
    echo "Number of Servers: $NUM_SERVERS"
    echo "Network Type:     $([ "$DOCKER_MODE" = "true" ] && echo "Docker (local)" || echo "$NETWORK_TYPE")"
    if [ "$DOCKER_MODE" != "true" ] && [ "$NETWORK_TYPE" = "dhcp" ] && [ -v MACS ]; then
        echo ""
        echo "MAC Addresses (for DHCP):"
        for server_name in "${!MACS[@]}"; do
            echo "  $server_name: ${MACS[$server_name]}"
        done
    fi
    echo "Keepalived VIP:   $KEEPALIVE_VIP"
    if [ "$DOCKER_MODE" != "true" ]; then
        echo "Gateway:          $GATEWAY_IP"
        echo "Subnet/CIDR:      $SUBNET_CIDR"
    fi
    echo ""
    echo "Servers:"
    for i in "${!DNS_IPS[@]}"; do
        local dns_num=$((i + 1))
        local server_name="dns$(printf "%02d" $dns_num)"
        local priority=$((150 - (i * 10)))
        local state="MASTER"
        if [ $i -gt 0 ]; then
            state="BACKUP"
        fi
        if [ "$DOCKER_MODE" = "true" ]; then
            printf "  %s (container %s): %s [priority: %d]\n" "$server_name" "${DNS_IPS[$i]}" "$state" "$priority"
        else
            printf "  %s (%s): %s [priority: %d]\n" "$server_name" "${DNS_IPS[$i]}" "$state" "$priority"
        fi
    done
    echo ""

    # Confirm before generating
    read -p "Proceed with configuration generation? [y/N]: " confirm
    if [[ ! $confirm =~ ^[Yy]$ ]]; then
        error "Setup cancelled"
    fi

    # Generate configurations
    header "Generating Configurations"

    generate_inventory "$SSH_USER" "${DNS_IPS[@]}"
    if [ "$DOCKER_MODE" != "true" ]; then
        generate_keepalived_playbook "$NUM_SERVERS" "$KEEPALIVE_VIP" "${DNS_IPS[@]}"
        update_zones_config "$NUM_SERVERS" "$KEEPALIVE_VIP" "$GATEWAY_IP" "$SUBNET_CIDR" "${DNS_IPV6S[@]}" "${DNS_IPS[@]}"
    else
        update_zones_config "$NUM_SERVERS" "$KEEPALIVE_VIP" "" "" "${DNS_IPS[@]}"
    fi

    if [ "$DOCKER_MODE" = "true" ]; then
        # Generate Docker Compose
        generate_docker_compose "$NUM_SERVERS" "$KEEPALIVE_VIP" "${DNS_IPS[@]}"

        # Create SSH test keypair if it doesn't exist
        mkdir -p "$SCRIPT_DIR/docker/test-keys"
        if [ ! -f "$SCRIPT_DIR/docker/test-keys/id_rsa" ]; then
            log "Generating test SSH keypair..."
            ssh-keygen -t rsa -b 4096 -f "$SCRIPT_DIR/docker/test-keys/id_rsa" -N "" -C "dnsmasq-ui-test"
            success "SSH keypair generated"
        fi
    fi

    echo ""
    header "Setup Complete! ✓"

    echo "Configuration files generated:"
    echo "  ✓ ansible/inventory.ini"
    if [ "$DOCKER_MODE" != "true" ]; then
        echo "  ✓ ansible/dnsmasq-setup.yml"
    else
        echo "  ✓ docker/dns-cluster.yml"
        echo "  ✓ docker/test-keys/id_rsa (SSH keypair)"
    fi
    echo "  ✓ zones.json (updated)"
    echo ""
    echo "Next steps:"
    echo ""

    if [ "$DOCKER_MODE" = "true" ]; then
        echo "1. Build and start the test cluster:"
        echo "   cd docker"
        echo "   ./build-test-cluster.sh"
        echo ""
        echo "2. Verify the cluster is running:"
        echo "   docker ps"
        echo ""
        echo "3. Test DNS queries:"
        echo "   dig @172.20.0.250 example.com"
        echo ""
        echo "4. To stop the cluster:"
        echo "   docker compose -f docker/dns-cluster.yml down"
        echo ""
    else
        echo "1. Review the generated files:"
        echo "   cat ansible/inventory.ini"
        echo "   cat ansible/dnsmasq-setup.yml"
        echo ""
        echo "2. Deploy with Ansible:"
        echo "   cd ansible"
        echo "   ansible-playbook -i inventory.ini dnsmasq-setup.yml"
        echo ""
        echo "3. Or use the deployment script:"
        echo "   cd .."
        echo "   ./deploy-keepalived.sh all"
        echo ""
        echo "4. Generate cloud-init files (for VM deployments):"
        echo "   ./generate-cloud-init.sh"
        echo ""
        echo "5. Verify deployment:"
        echo "   curl http://192.168.0.250:5000/api/status"
        echo ""
    fi
}

# Run setup
main "$@"
