# DNS Timeout Debugging Guide

**Date**: 2026-03-15
**Status**: 🔧 ONGOING - Root cause identified but solution incomplete
**Issue**: dnsmasq not responding to ANY DNS queries despite zones loading correctly
**Scope**: This is about the Docker dns-node test cluster (`docker-compose.yml`,
172.20.0.x), not the real production servers (192.168.0.231-233). For the
production middle-01 record incident, see the Troubleshooting section in
README.md.

---

## Current State (After This Session)

### ✅ What's Working
- WireGuard mesh network operational (ping works)
- zones.json parsing fixed (records load to `/etc/dnsmasq.d/zones.conf`)
- dnsmasq process starts and runs
- dnsmasq listening on 0.0.0.0:53 (TCP and UDP)
- Container networking configured correctly
- dnsmasq config file valid

### ❌ What's NOT Working
- DNS queries timeout on localhost (127.0.0.1:53)
- DNS queries timeout over tunnel (10.99.0.1:53)
- dnsmasq appears to be non-responsive despite running

---

## Verification Steps Already Completed

```bash
# 1. ✅ Confirmed zones.conf has records
docker exec dns01 cat /etc/dnsmasq.d/zones.conf | wc -l
# Result: 11 lines of DNS records

# 2. ✅ Confirmed dnsmasq process running
docker exec dns01 ps aux | grep dnsmasq
# Result: /usr/sbin/dnsmasq -C /etc/dnsmasq.conf

# 3. ✅ Confirmed dnsmasq listening on port 53
docker exec dns01 ss -tlnup | grep 53
# Result: 0.0.0.0:53 LISTEN (TCP and UDP)

# 4. ✅ Confirmed container localhost configured
docker exec dns01 ip addr | grep 127
# Result: inet 127.0.0.1/8 scope host lo

# 5. ❌ DNS queries timeout
docker exec dns01 dig @127.0.0.1 dns01.ad.alshowto.com +short
# Result: ;; communications error to 127.0.0.1#53: timed out

# 6. ❌ Even dnsmasq restart fails
docker exec dns01 pkill -HUP dnsmasq
docker exec dns01 dig @127.0.0.1 dns01.ad.alshowto.com +short
# Result: Still times out
```

---

## Possible Root Causes

### Hypothesis 1: dnsmasq Configuration Issue
**Evidence Against**:
- Config file is minimal and valid: `conf-dir=/etc/dnsmasq.d`, `listen-address=0.0.0.0`
- dnsmasq runs without errors
- No crash logs or error messages

### Hypothesis 2: Container Network Namespace Issue
**Evidence Against**:
- localhost loopback is configured
- Port 53 shows as LISTEN in netstat
- Other services (SSH, keepalived) work fine

### Hypothesis 3: dnsmasq Startup Timing Issue
**Evidence For**:
- Logs show "dnsmasq started (PID: 17)" early in boot
- Manual tests after restart still fail
- PID 108 (current dnsmasq) was started at 14:41, long after container started at 14:38

### Hypothesis 4: dnsmasq Process Stuck/Hung
**Evidence Against**:
- Process shows in `ps aux` with normal VSZ/RSS
- CPU usage is 0% (not hung)
- No zombie process indicators

---

## Recommended Debugging Steps for Next Session

### Step 1: Interactive Container Shell
```bash
# Access container shell and test directly
docker exec -it dns01 /bin/bash

# Inside container, run these tests:
ps aux | grep dnsmasq | grep -v grep
ss -tlnup | grep 53
cat /etc/dnsmasq.conf
cat /etc/dnsmasq.d/zones.conf | head -5
dig @127.0.0.1 google.com
nslookup google.com 127.0.0.1
```

### Step 2: Check Complete Startup Logs
```bash
docker logs dns01 2>&1 | head -50
docker logs dns01 2>&1 | tail -50
```

### Step 3: Run dnsmasq with Debug Output
```bash
docker exec dns01 pkill -9 dnsmasq
docker exec dns01 /usr/sbin/dnsmasq -C /etc/dnsmasq.conf --no-daemon --log-queries=extra 2>&1 | head -100 &
sleep 2
docker exec dns01 dig @127.0.0.1 google.com
```

### Step 4: Test Alternative DNS Port
```bash
docker exec dns01 /usr/sbin/dnsmasq -C /etc/dnsmasq.conf -p 5353 &
docker exec dns01 dig @127.0.0.1 -p 5353 google.com +short
```

---

## Summary

**What Was Accomplished**:
- ✅ Fixed zones.json parsing bug (zones as list not dict)
- ✅ Rebuilt containers with fix
- ✅ Confirmed DNS records load successfully
- ✅ Identified deeper DNS responsiveness issue

**What Still Needs Investigation**:
- Why dnsmasq doesn't respond to DNS queries despite listening on port 53
- Whether it's a startup timing, configuration, or process issue
- Why manual dnsmasq restarts don't help

**Confidence**: High that the issue is with dnsmasq's responsiveness, not with zones.json or network connectivity.
