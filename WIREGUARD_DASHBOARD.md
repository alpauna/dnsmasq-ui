# WireGuard Dashboard Integration

**Date**: 2026-03-15
**Feature**: WireGuard mesh status display in dnsmasq-ui dashboard
**Status**: ✅ COMPLETE

---

## Overview

The dnsmasq-ui dashboard now displays comprehensive WireGuard mesh network status alongside DNS server information. This provides operators with real-time visibility into the encrypted tunnel network status.

---

## Dashboard Display

### Stats Section
When WireGuard is enabled, a new stat card shows overall mesh status:
```
┌─────────────────┐
│ WireGuard Mesh  │
│     3/3         │  ← Green if all servers up
│                 │     Orange if partial
│                 │     Red if all down
└─────────────────┘
```

The number indicates: **servers_up / total_servers**

Color codes:
- 🟢 **Green**: All servers connected (3/3 or 2/3)
- 🟠 **Orange**: Partial connectivity
- 🔴 **Red**: No servers connected

### Server Cards
Each DNS server card now shows:

```
┌──────────────────────┐
│   dns01.example.com  │
├──────────────────────┤
│ IP Address:          │
│ 172.20.0.231         │
├──────────────────────┤
│ Status: ● Online     │
├──────────────────────┤
│ Tunnel IP:           │
│ 10.99.0.1            │
├──────────────────────┤
│ WireGuard:           │
│ ✓ Up (10.99.0.1)     │
│ 2 peers connected    │
└──────────────────────┘
```

### WireGuard Status Details

**When Up** (Green):
- ✓ Up with tunnel IP shown
- Number of connected peers displayed
- Example: `✓ Up (10.99.0.1)` with `2 peers connected`

**When Down** (Red):
- ✗ Down status displayed
- Error message shown
- Example: `✗ Down` with `Error: Connection refused`

**When Not Configured** (Gray):
- ○ Not configured status
- No peer information
- Example: `○ Not configured`

---

## Configuration

### Enable WireGuard in zones.json
```json
{
  "global": {
    "wireguard": {
      "enabled": true,
      "mesh_subnet": "10.99.0.0/24",
      "listen_port": 51820,
      "persistent_keepalive": 25
    }
  }
}
```

### Tunnel IP Configuration
Each server should have a tunnel IP assigned:
```json
{
  "servers": {
    "dns01": {
      "ip": "172.20.0.231",
      "hostname": "dns01",
      "wireguard": {
        "tunnel_ip": "10.99.0.1",
        "public_key": "...",
        "listen_port": 51820
      }
    }
  }
}
```

---

## API Endpoints

### GET /api/status
Returns comprehensive server status including WireGuard:

```json
{
  "servers": {
    "dns01": {
      "ip": "172.20.0.231",
      "hostname": "dns01",
      "online": true,
      "dnsmasq": "active",
      "tunnel_ip": "10.99.0.1",
      "wireguard": {
        "wg0_up": true,
        "peers_connected": 2,
        "interface_ip": "10.99.0.1/32",
        "error": ""
      },
      "keepalived": {
        "running": true,
        "status": "MASTER",
        "vip": "192.168.0.252"
      }
    }
  },
  "vip": "192.168.0.252",
  "wg_enabled": true
}
```

### GET /api/wireguard/status
Returns only WireGuard status for all servers:

```json
{
  "dns01": {
    "wg0_up": true,
    "peers_connected": 2,
    "interface_ip": "10.99.0.1/32",
    "error": ""
  },
  "dns02": {
    "wg0_up": true,
    "peers_connected": 2,
    "interface_ip": "10.99.0.2/32",
    "error": ""
  }
}
```

---

## Auto-Refresh

Dashboard status updates automatically every 30 seconds:
- Server online/offline status
- Keepalived MASTER/STANDBY status
- WireGuard tunnel status
- Peer connection counts

Manual refresh available by reloading the page (F5).

---

## Troubleshooting

### WireGuard Status Shows "Not Configured"
- Verify WireGuard is enabled in zones.json
- Check that keys have been generated
- Ensure config was deployed to servers

### WireGuard Status Shows "Down"
- Check SSH connectivity to server
- Verify WireGuard interface exists: `docker exec dns01 ip addr show wg0`
- Check for errors in deployment

### Peers Not Showing Connected
- Verify all peers are in wg show output: `docker exec dns01 wg show`
- Check latest handshake timestamp (should be recent)
- Verify firewall rules if WG_FIREWALL_ENABLE=true

---

## Code Changes

### Python Backend (app-multi-zone.py)

**index() Route**:
- Added WireGuard status fetching for each server
- Added tunnel IP display
- Passes wg_enabled flag to template

**api_status() Endpoint**:
- Includes tunnel_ip for each server
- Includes full wireguard object with status
- Adds wg_enabled boolean to response

### Frontend (templates/dashboard-v2.html)

**Stats Section**:
- New stat card for WireGuard mesh overview
- Shows servers_up/total_servers
- Color-coded status indicator

**Server Cards**:
- New tunnel IP field
- WireGuard status display (when enabled)
- Peer connection count
- Interface IP address

**JavaScript**:
- Fetches /api/wireguard/status
- Updates WireGuard stat card with summary
- Updates individual server WireGuard status
- Error handling for unavailable endpoints

---

## User Experience

### When WireGuard is Disabled
- No WireGuard information displayed
- Dashboard functions normally for DNS/Keepalived
- No performance impact

### When WireGuard is Enabled
- Instant visibility of mesh status
- Color-coded health indicators
- Automatic monitoring every 30 seconds
- Peer connection visibility per server

---

## Future Enhancements

### Possible Additions
1. **Bandwidth Monitoring**: Show data transfer rates over tunnel
2. **Peer Details**: Click to see detailed peer information
3. **Manual Deploy**: Button to deploy/restart WireGuard
4. **Configuration View**: Display WireGuard config on dashboard
5. **Historical Metrics**: Graph of tunnel uptime/stability
6. **Alerts**: Notifications when mesh goes down

---

## Summary

The WireGuard dashboard integration provides operators with real-time visibility into the encrypted mesh tunnel network, complementing the existing DNS server and Keepalived monitoring. Status updates automatically every 30 seconds with clear visual indicators of mesh health.

**Commit**: `cfd4d03 - Feature: Add WireGuard mesh status display to dashboard`
