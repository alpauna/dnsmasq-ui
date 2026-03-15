# WireGuard DNS Access with Firewall Rules

## Overview

Secure DNS access via WireGuard mesh network with deny-all-except-supported-ports firewall policy.

Allows remote clients to connect to dnsmasq via WireGuard tunnel (10.99.0.0/24) while restricting traffic to DNS ports only (53/TCP and 53/UDP). All other traffic is blocked at the firewall level.

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│  Client (Remote)                                            │
│  - Connects via WireGuard VPN (10.99.0.x)                  │
│  - Can resolve DNS queries on 10.99.0.1/2/3                │
└────────────┬────────────────────────────────────────────────┘
             │
             │ WireGuard Encrypted Tunnel (10.99.0.0/24)
             │
┌────────────▼────────────────────────────────────────────────┐
│  DNS Nodes (dns01, dns02, dns03)                           │
│  ┌──────────────────────────────────────────────────────┐  │
│  │ eth0: 172.20.0.231-233  (Docker network)            │  │
│  │ wg0:  10.99.0.1-3       (WireGuard mesh)            │  │
│  │                                                      │  │
│  │ Firewall Rules (iptables):                         │  │
│  │ - ACCEPT DNS (53/TCP, 53/UDP) on wg0               │  │
│  │ - ACCEPT SSH (22) on eth0 (for management)         │  │
│  │ - DROP all other traffic (implicit deny)           │  │
│  │                                                      │  │
│  │ dnsmasq:   Listening on wg0:53                     │  │
│  │ keepalived: VIP on eth0:172.20.0.252              │  │
│  │ sshd:      Listening on eth0:22                    │  │
│  └──────────────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────────────────┘
```

## Implementation Plan

### Phase 1: Firewall Rules (entrypoint.sh)

Add iptables rules to DNS node entrypoint to enforce deny-all-except-DNS-ports.

**Rules to implement:**

```bash
# DNS Node Firewall Configuration
# ===============================

# 1. Set default policy to DROP (deny all)
iptables -P INPUT DROP
iptables -P FORWARD DROP
iptables -P OUTPUT ACCEPT

# 2. Allow loopback (required for services to function)
iptables -A INPUT -i lo -j ACCEPT

# 3. Allow SSH on eth0 (management access)
iptables -A INPUT -i eth0 -p tcp --dport 22 -j ACCEPT

# 4. Allow DNS on wg0 ONLY (main feature)
iptables -A INPUT -i wg0 -p tcp --dport 53 -j ACCEPT
iptables -A INPUT -i wg0 -p udp --dport 53 -j ACCEPT

# 5. Allow keepalived VRRP on eth0 (cluster communication)
iptables -A INPUT -i eth0 -p 112 -j ACCEPT

# 6. Allow ICMP for diagnostics (optional, can remove for strict mode)
iptables -A INPUT -p icmp --icmp-type echo-request -j ACCEPT

# 7. Allow established connections (important for replies)
iptables -A INPUT -m state --state ESTABLISHED,RELATED -j ACCEPT

# 8. IPv6 equivalent (ip6tables)
ip6tables -P INPUT DROP
ip6tables -P FORWARD DROP
ip6tables -P OUTPUT ACCEPT
ip6tables -A INPUT -i lo -j ACCEPT
ip6tables -A INPUT -i wg0 -p tcp --dport 53 -j ACCEPT
ip6tables -A INPUT -i wg0 -p udp --dport 53 -j ACCEPT
ip6tables -A INPUT -m state --state ESTABLISHED,RELATED -j ACCEPT
```

**Persistence:** Use `iptables-persistent` or write rules to `/etc/iptables/rules.v4` to survive container restart.

### Phase 2: dnsmasq Configuration

Already implemented in `app-multi-zone.py`:
- `generate_wg_dnsmasq_config()` generates config binding dnsmasq to wg0
- dnsmasq listens on tunnel IP (10.99.0.1, 10.99.0.2, 10.99.0.3)

**Current config:**
```ini
interface=wg0
listen-address=10.99.0.1
```

**Enhancement:** Add explicit deny rules in dnsmasq.conf to reject non-DNS queries.

### Phase 3: API Endpoints

Add new Flask endpoints for firewall management:

```
POST   /api/firewall/rules              - Get firewall rules status
GET    /api/firewall/rules              - Fetch current iptables rules
POST   /api/firewall/enable-wg-dns      - Enable WireGuard DNS + firewall
POST   /api/firewall/disable-wg-dns     - Disable and restore default rules
GET    /api/firewall/stats              - Get traffic stats (iptables counters)
```

### Phase 4: SSH/Admin Access Preservation

Ensure admin access via SSH on eth0 is never blocked:

```bash
# SSH on management network (eth0) remains open
iptables -A INPUT -i eth0 -p tcp --dport 22 -j ACCEPT

# Only restrict WireGuard interface (wg0) to DNS
iptables -A INPUT -i wg0 -p tcp --dport 53 -j ACCEPT
iptables -A INPUT -i wg0 -p udp --dport 53 -j ACCEPT
iptables -A INPUT -i wg0 -j DROP
```

## Client Usage Example

```bash
# On client machine with WireGuard tunnel to 10.99.0.1

# DNS query via WireGuard tunnel
dig @10.99.0.1 example.ad.alshowto.com

# Should work (DNS)
$ dig @10.99.0.1 +short google.com
142.250.185.46

# Should fail (non-DNS port)
$ ssh root@10.99.0.1
# Connection refused - firewall blocks
```

## Configuration Files to Modify

### 1. `docker/dns-node/entrypoint.sh`

Add firewall rules setup section:

```bash
# ============================================================================
# Firewall Configuration (WireGuard DNS Access Control)
# ============================================================================

echo "[*] Configuring firewall rules..."

# Detect WireGuard interface presence
if [ -z "$WG_INTERFACE" ]; then
    WG_INTERFACE="wg0"
fi

# Helper function to set firewall rules
setup_firewall() {
    # Default deny policy
    iptables -P INPUT DROP 2>/dev/null || true
    iptables -P FORWARD DROP 2>/dev/null || true
    iptables -P OUTPUT ACCEPT 2>/dev/null || true

    # Allow loopback
    iptables -A INPUT -i lo -j ACCEPT 2>/dev/null || true

    # Allow SSH on management interface (eth0)
    iptables -A INPUT -i eth0 -p tcp --dport 22 -j ACCEPT 2>/dev/null || true

    # Allow DNS on WireGuard interface ONLY
    iptables -A INPUT -i ${WG_INTERFACE} -p tcp --dport 53 -j ACCEPT 2>/dev/null || true
    iptables -A INPUT -i ${WG_INTERFACE} -p udp --dport 53 -j ACCEPT 2>/dev/null || true

    # Allow keepalived VRRP
    iptables -A INPUT -i eth0 -p 112 -j ACCEPT 2>/dev/null || true

    # Allow established connections
    iptables -A INPUT -m state --state ESTABLISHED,RELATED -j ACCEPT 2>/dev/null || true

    # IPv6
    ip6tables -P INPUT DROP 2>/dev/null || true
    ip6tables -A INPUT -i lo -j ACCEPT 2>/dev/null || true
    ip6tables -A INPUT -i ${WG_INTERFACE} -p tcp --dport 53 -j ACCEPT 2>/dev/null || true
    ip6tables -A INPUT -i ${WG_INTERFACE} -p udp --dport 53 -j ACCEPT 2>/dev/null || true
    ip6tables -A INPUT -m state --state ESTABLISHED,RELATED -j ACCEPT 2>/dev/null || true
}

# Only apply firewall if WG_FIREWALL_ENABLE is set
if [ "${WG_FIREWALL_ENABLE:-false}" = "true" ]; then
    setup_firewall
    echo "[+] Firewall rules configured (WireGuard DNS access control)"
else
    echo "[*] Firewall disabled (set WG_FIREWALL_ENABLE=true to enable)"
fi
```

### 2. `docker-compose.yml`

Add environment variable to enable feature:

```yaml
dns01:
  environment:
    - WG_FIREWALL_ENABLE=false  # Set to true when WireGuard is deployed
    - WG_INTERFACE=wg0
```

### 3. `app-multi-zone.py`

New methods for firewall management:

```python
def enable_wg_firewall(self, server_ip):
    """Deploy and enable WireGuard DNS firewall rules."""
    # SSH to server and configure iptables

def disable_wg_firewall(self, server_ip):
    """Remove WireGuard firewall rules, restore default."""

def get_firewall_status(self, server_ip):
    """Get current firewall rule status and traffic stats."""
```

## Security Considerations

### Strengths
- **Deny-all approach**: Default DROP policy blocks everything except explicitly allowed
- **Interface isolation**: Rules are per-interface (wg0 vs eth0)
- **Port specificity**: Only DNS ports (53/TCP, 53/UDP) allowed on tunnel
- **Connection tracking**: Established connections tracked to prevent state confusion
- **SSH preserved**: Admin access via eth0 never blocked

### Edge Cases

1. **IPv4-only rules don't block IPv6**: Implement ip6tables rules in parallel
2. **Established connections**: Allow return traffic with `-m state --state ESTABLISHED,RELATED`
3. **WireGuard before iptables**: Load wg0 interface before setting firewall (done in app)
4. **Stateless UDP DNS**: Some clients use UDP without state tracking - mitigated by explicit rules

### Optional Hardening

```bash
# Rate limit DNS queries (prevent amplification attacks)
iptables -A INPUT -i wg0 -p udp --dport 53 -m limit \
  --limit 1000/minute --limit-burst 2000 -j ACCEPT

# Log dropped packets (debugging)
iptables -A INPUT -j LOG --log-prefix "DROPPED: "
```

## Testing Procedure

### 1. Enable WireGuard + Firewall on one node

```bash
# On builder:
curl -X POST http://192.168.0.253:5000/api/wireguard/deploy
curl -X POST http://192.168.0.253:5000/api/firewall/enable-wg-dns
```

### 2. From client with WireGuard tunnel

```bash
# Should work - DNS query
dig @10.99.0.1 google.com

# Should timeout/fail - SSH attempt
ssh root@10.99.0.1

# Should timeout/fail - HTTP attempt
curl http://10.99.0.1:5000/api/zones
```

### 3. Verify firewall rules

```bash
docker exec dns01 iptables -L -n
docker exec dns01 ip addr show wg0
docker exec dns01 netstat -tlnup | grep 53
```

## Deployment Timeline

**Phase 1 (Current)**: Firewall rule infrastructure - 2-3 hours
**Phase 2 (Current)**: API endpoints - 1-2 hours
**Phase 3 (Follow-up)**: Testing & documentation - 1 hour
**Phase 4 (Optional)**: Rate limiting & DDoS protection - 1-2 hours

## Related Features

- WireGuard mesh (✅ Implemented)
- dnsmasq multi-zone support (✅ Implemented)
- SSH key management (✅ Implemented)
- API status/monitoring (✅ Implemented)
- **Firewall access control** (🔄 This proposal)
- Key rotation policies (Planned)
- Client onboarding wizard (Planned)
