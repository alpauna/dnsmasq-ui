# DNS Query Timeout Fix

**Date**: 2026-03-15
**Issue**: DNS queries timeout over WireGuard tunnel while ping works
**Status**: 🔧 In Progress - Fix applied, containers rebuilding
**Scope**: This is about the Docker dns-node test cluster (`docker-compose.yml`,
172.20.0.x), not the real production servers (192.168.0.231-233). For the
production middle-01 record incident, see the Troubleshooting section in
README.md.

---

## Root Cause Analysis

### Symptoms
- Ping over WireGuard tunnel: ✅ Works (0% packet loss)
- DNS queries over tunnel: ❌ Timeout
- DNS queries on localhost: ❌ Timeout
- dnsmasq process: ✅ Running
- dnsmasq listening ports: ✅ Port 53 active on all interfaces

### Investigation Results

**Container logs** revealed:
```
[!] Failed to load zones.json: 'list' object has no attribute 'items'
```

**Root Cause**: Mismatch between zones.json schema and Python parsing code

**zones.json structure** (actual):
```json
{
  "zones": [
    {
      "name": "ad.alshowto.com",
      "records": [ ... ]
    },
    {
      "name": "internal.alshowto.com",
      "records": [ ... ]
    }
  ]
}
```

**entrypoint.sh Python code** (was expecting):
```python
config['zones'].items()  # ← Tries to call .items() on a list, which fails
```

### Why DNS Doesn't Work

When zones.json parsing fails:
1. No DNS records are written to `/etc/dnsmasq.d/zones.conf`
2. dnsmasq starts but has an empty zone file
3. All DNS queries fall through to upstream DNS (1.1.1.1, 8.8.8.8)
4. Upstream DNS can't be reached (or timeout)
5. Result: ALL DNS queries timeout, even for local zones

---

## Solution Applied

### File: `docker/dns-node/entrypoint.sh` (lines 131-160)

**Fixed the zones.json parsing to handle both formats**:

```python
# NEW: Check if zones is a list vs dict
zones_list = config['zones']
if isinstance(zones_list, list):
    # Handle zones as a list (current format)
    for zone_data in zones_list:
        # Process records...
else:
    # Fallback for dict-based zones (legacy)
    for zone_name, zone_data in zones_list.items():
        # Process records...
```

**Benefits**:
- ✅ Handles current zones.json structure (list of zones)
- ✅ Backward compatible with legacy dict format
- ✅ Proper error handling with exception capture
- ✅ DNS records will now load correctly on startup

---

## Deployment Steps

### 1. Build Docker Image with Fix
```bash
cd /opt/dnsmasq-ui
docker-compose build --no-cache dns01 dns02 dns03
```

### 2. Restart Containers
```bash
docker-compose restart dns01 dns02 dns03
```

### 3. Verify Fix Applied
```bash
# Check container logs for success message
docker logs dns01 | grep -E "(DNS records|Failed to load)"

# Expected output: "[+] DNS records configured"
```

---

## Testing Plan

### After Containers Restart

**1. Verify DNS Records Loaded**
```bash
docker exec dns01 cat /etc/dnsmasq.d/zones.conf | head -20
# Should show: address=/10g-sw01.ad.alshowto.com/2604:7a00...
#              address=/dns01.ad.alshowto.com/192.168.0.231
#              cname=esphome.ad.alshowto.com,middle-01.ad.alshowto.com
#              (etc.)
```

**2. Test Local Zone Resolution (localhost)**
```bash
docker exec dns01 dig @127.0.0.1 10g-sw01.ad.alshowto.com +short
# Expected: 2604:7a00:ea40:5630:5ea6:e6ff:fe27:417c (AAAA record)
```

**3. Test Local Zone via WireGuard Tunnel**
```bash
docker exec dns02 dig @10.99.0.1 10g-sw01.ad.alshowto.com +short
# Expected: 2604:7a00:ea40:5630:5ea6:e6ff:fe27:417c

docker exec dns03 dig @10.99.0.1 dns01.ad.alshowto.com +short
# Expected: 192.168.0.231
```

**4. Test Upstream DNS Forwarding**
```bash
docker exec dns02 dig @10.99.0.1 google.com +short
# Expected: (Google IP addresses, e.g., 142.251.41.14)
```

**5. Test from Container to Container DNS**
```bash
docker exec dns02 ping -c 2 10g-sw01.ad.alshowto.com
# Should resolve via DNS and ping succeeds (or ICMP blocked - that's OK)
```

---

## Implementation Details

### Change Summary
- **File Modified**: `docker/dns-node/entrypoint.sh`
- **Lines Changed**: 131-160 (Python parsing section)
- **Type**: Bug fix
- **Impact**: Enables DNS queries over all interfaces including WireGuard

### Code Changes
```diff
- for zone_name, zone_data in config['zones'].items():
+ zones_list = config['zones']
+ if isinstance(zones_list, list):
+     for zone_data in zones_list:
+         # ...
+ else:
+     for zone_name, zone_data in zones_list.items():
+         # ...
```

### Backward Compatibility
- ✅ Current zones.json structure (list) supported
- ✅ Legacy dict structure still works via fallback
- ✅ No breaking changes to configuration format

---

## Container Build Status

**Rebuild Started**: 2026-03-15 ~14:40 UTC
**Expected Duration**: 3-5 minutes per container
**Current Phase**: Building from Dockerfile

```
Step 1/8: FROM debian:12-slim
Step 2/8: RUN apt-get update...
Step 3/8: RUN apt-get install dnsmasq keepalived...
Step 4/8: RUN mkdir -p /run/sshd...
Step 5/8: COPY entrypoint.sh...  ← NEW VERSION COPIED
Step 6/8: RUN chmod +x...
Step 7/8: EXPOSE 22 53/tcp 53/udp
Step 8/8: ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
```

---

## Expected Results After Fix

### Container Logs
```
[*] Processing DNS records from zones.json...
[+] DNS records configured  ← NEW: This message will appear
[*] Starting services...
```

### dnsmasq Config
```
$ docker exec dns01 cat /etc/dnsmasq.d/zones.conf
address=/10g-sw01.ad.alshowto.com/2604:7a00:ea40:5630:5ea6:e6ff:fe27:417c
address=/10g-sw02.ad.alshowto.com/2604:7a00:ea40:5630:56af:97ff:fe8f:c7a7
address=/dns01.ad.alshowto.com/192.168.0.231
address=/dns02.ad.alshowto.com/192.168.0.232
address=/dns03.ad.alshowto.com/192.168.0.233
address=/middle-01.ad.alshowto.com/192.168.0.252
cname=esphome.ad.alshowto.com,middle-01.ad.alshowto.com
cname=frigate.ad.alshowto.com,middle-01.ad.alshowto.com
cname=ha.ad.alshowto.com,middle-01.ad.alshowto.com
cname=portainer.ad.alshowto.com,middle-01.ad.alshowto.com
cname=proxmox.ad.alshowto.com,middle-01.ad.alshowto.com
```

### DNS Resolution
```
$ dig @127.0.0.1 10g-sw01.ad.alshowto.com

; <<>> DiG 9.18.39 <<>> @127.0.0.1 10g-sw01.ad.alshowto.com
; (1 server found)
;; global options: +cmd
;; Got answer:
;; ->>HEADER<<- opcode: QUERY, status: NOERROR, id: 12345
;; flags: qr aa rd; QUERY: 1, ANSWER: 1, AUTHORITY: 0, ADDITIONAL: 0

;; QUESTION SECTION:
;10g-sw01.ad.alshowto.com.  IN  AAAA

;; ANSWER SECTION:
10g-sw01.ad.alshowto.com. 3600 IN AAAA 2604:7a00:ea40:5630:5ea6:e6ff:fe27:417c

;; Query time: 1 msec
;; SERVER: 127.0.0.1#53(127.0.0.1)
```

---

## Verification Checklist

After containers restart, verify:

- [ ] Container logs show `[+] DNS records configured` (not the error)
- [ ] `/etc/dnsmasq.d/zones.conf` contains DNS records (not empty)
- [ ] DNS queries on localhost complete (no timeout)
- [ ] DNS queries over tunnel complete (no timeout)
- [ ] Ping over tunnel still works
- [ ] All three DNS servers have the same records
- [ ] WireGuard interfaces still up and handshaking
- [ ] SSH access still working

---

## Files Modified

- `docker/dns-node/entrypoint.sh` — Fixed zones.json parsing
- `WIREGUARD_MESH_COMPLETION.md` — Updated with DNS debugging info
- This file — `DNS_QUERY_FIX.md`

---

## Git Commits

```
345381f - Fix: Handle zones.json as list in dnsmasq config parsing
```

---

## Status

**Current**: Containers rebuilding with fix...
**Next**: Verify DNS queries work over tunnel
**Then**: Update WireGuard mesh documentation with complete status

