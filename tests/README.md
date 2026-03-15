# DNS Cluster Test Suite

Comprehensive testing suite for DNS and Keepalived cluster validation.

## Overview

This test suite includes:

1. **DNS Stress Test** - High-load DNS query testing
2. **Keepalived Failover Test** - Master failure simulation and recovery
3. **Test Runner** - Orchestrates all tests with summary reporting

## Quick Start

```bash
# Run all tests
./run-all-tests.sh

# Run only stress test
./run-all-tests.sh --stress

# Run only failover test
./run-all-tests.sh --failover

# Run stress test with high load
./run-all-tests.sh --stress --queries 1000 --threads 20
```

## Test Files

### 1. dns-stress-test.sh

Stress tests the DNS cluster with concurrent queries.

**Purpose:**
- Verify DNS performance under load
- Test reliability with hundreds/thousands of queries
- Measure throughput (queries per second)
- Detect timeout and failure rates

**What it tests:**
- Query success rate (99%+ expected)
- Response times
- Timeout handling
- Performance metrics per domain

**Usage:**
```bash
./dns-stress-test.sh [OPTIONS]

Options:
  -v, --vip VIP              VIP to test (default: 192.168.0.250)
  -q, --queries NUM          Queries per domain (default: 100)
  -t, --threads NUM          Concurrent threads (default: 10)
  -d, --domain DOMAIN        Test specific domain
  -h, --help                 Show help

Examples:
  ./dns-stress-test.sh                    # Default: 100 queries, 10 threads
  ./dns-stress-test.sh --queries 500      # 500 queries per domain
  ./dns-stress-test.sh --queries 1000 --threads 20  # High stress
  ./dns-stress-test.sh --domain dns01.ad.alshowto.com  # Single domain
```

**Expected Results:**
```
Overall Success Rate:  99.5% (995/1000)
Failed Queries:        0
Timeouts:              5
Total Test Time:       8234ms
Throughput:            121 QPS

Per-Domain Results:
dns01.ad.alshowto.com               250/250   (100.0%)
dns02.ad.alshowto.com               250/250   (100.0%)
dns03.ad.alshowto.com               245/250    (98.0%)
middle-01.ad.alshowto.com           250/250   (100.0%)
```

**Pass Criteria:**
- Success rate ≥ 99%: PASSED
- Success rate ≥ 95%: PASSED (with warnings)
- Success rate < 95%: FAILED

### 2. keepalived-failover.yml

Ansible playbook that tests keepalived failover behavior.

**Purpose:**
- Verify MASTER detection and VIP assignment
- Simulate master failure (stop keepalived on dns01)
- Confirm automatic failover to backup (dns02)
- Verify master recovery and preemption (dns01 resumes MASTER)
- Ensure DNS service continuity throughout

**What it tests:**

**Phase 1 - Baseline:**
- Verifies dns01 is MASTER with priority 150
- Confirms dns02 and dns03 are STANDBY
- Verifies VIP (192.168.0.250) is assigned to dns01
- Tests DNS resolution before failover

**Phase 2 - Failover:**
- Stops keepalived on dns01 (simulates failure)
- Waits 10 seconds for detection and failover
- Confirms dns02 becomes MASTER
- Verifies VIP moved to dns02
- Tests DNS continues to resolve via new MASTER

**Phase 3 - Recovery:**
- Restarts keepalived on dns01 (recovery)
- Waits for dns01 to resume MASTER role (preemption)
- Confirms VIP returned to dns01
- Tests DNS resolution after recovery

**Expected Results:**
```
BASELINE CLUSTER STATUS (BEFORE):
dns01: MASTER (Online: True)
dns02: STANDBY (Online: True)
dns03: STANDBY (Online: True)
VIP: 192.168.0.250

CLUSTER STATUS (AFTER DNS01 SHUTDOWN):
dns01: INACTIVE (Online: False)
dns02: MASTER (Online: True)
dns03: STANDBY (Online: True)
VIP: 192.168.0.250

CLUSTER STATUS (AFTER DNS01 RECOVERY):
dns01: MASTER (Online: True)
dns02: STANDBY (Online: True)
dns03: STANDBY (Online: True)
VIP: 192.168.0.250
```

**Pass Criteria:**
- ✅ dns02 becomes MASTER when dns01 fails
- ✅ VIP moves to dns02
- ✅ DNS resolution works during failover
- ✅ dns01 resumes MASTER after recovery
- ✅ VIP returns to dns01
- ✅ DNS resolution works after recovery

### 3. run-all-tests.sh

Master test runner that orchestrates all tests.

**Purpose:**
- Run all tests in sequence
- Provide unified reporting
- Check prerequisites
- Validate test environment

**Usage:**
```bash
./run-all-tests.sh [OPTIONS]

Options:
  -a, --all                   Run all tests (default)
  -s, --stress                Run only stress test
  -f, --failover              Run only failover test
  -v, --vip VIP               VIP address (default: 192.168.0.250)
  -q, --queries NUM           Stress queries (default: 100)
  -t, --threads NUM           Stress threads (default: 10)
  --no-failover-recovery      Skip recovery after failover
  -h, --help                  Show help

Examples:
  ./run-all-tests.sh                      # Run all tests
  ./run-all-tests.sh --stress --queries 500  # Stress test only
  ./run-all-tests.sh --failover           # Failover test only
```

## Prerequisites

### Required
- `dig` command (install: `apt-get install dnsutils`)
- SSH access to all DNS servers
- Network connectivity to VIP (192.168.0.250)

### For Failover Test
- Ansible (install: `pip install ansible`)
- SSH key configured for passwordless access

### For All Tests
```bash
# Ubuntu/Debian
sudo apt-get install -y dnsutils openssh-client
pip install ansible

# Verify
dig --version
ssh -V
ansible --version
```

## Running the Tests

### Simple Test (DNS Stress Only)
```bash
cd tests
./dns-stress-test.sh
```

**Output:**
- Per-domain query results
- Success/failure rates
- Performance metrics
- Final verdict

### Complete Test Suite
```bash
cd tests
./run-all-tests.sh
```

**Output:**
- Prerequisite checks
- DNS stress test results
- Keepalived failover simulation
- Unified test summary

### High-Load Stress Test
```bash
cd tests
./run-all-tests.sh --stress --queries 1000 --threads 20
```

**Output:**
- High-volume test with 1000 queries per domain
- 20 concurrent threads
- Performance characteristics
- Peak throughput measurement

### Failover Test Only
```bash
cd tests
./run-all-tests.sh --failover
```

**Output:**
- Baseline cluster status
- Failover simulation (dns01 shutdown)
- Recovery verification (dns01 restart)
- VIP movement tracking
- DNS continuity confirmation

## Interpreting Results

### DNS Stress Test

**Success Rate:**
- **≥ 99%**: Excellent - Cluster is very reliable
- **95-99%**: Good - Cluster is working well, minor timeouts acceptable
- **< 95%**: Poor - Cluster has issues, investigate further

**Throughput (QPS):**
- **≥ 100 QPS**: Good baseline performance
- **≥ 200 QPS**: High performance under concurrent load

**Timeouts:**
- **0**: Perfect (expected)
- **< 1%**: Acceptable (occasional network delays)
- **> 1%**: May indicate slow responses or network issues

### Keepalived Failover Test

**Expected Sequence:**
1. dns01 is MASTER (priority 150)
2. dns01 keepalived stops → dns02 becomes MASTER
3. dns01 keepalived restarts → dns01 becomes MASTER (preemption)
4. VIP assignment follows MASTER role
5. DNS resolution works throughout

**Common Issues:**

| Issue | Cause | Solution |
|-------|-------|----------|
| dns02 doesn't become MASTER | Priority not configured correctly | Check /etc/keepalived/keepalived.conf |
| VIP stays on dns01 after shutdown | Track process failed | Verify dnsmasq is running on dns02 |
| dns01 doesn't resume MASTER | Preemption disabled | Verify priority 150 on dns01 |
| DNS doesn't resolve during failover | VIP assignment delay | Check keepalive_advert_int (1 second recommended) |

## Troubleshooting

### Test Prerequisites Fail
```bash
# Check dig
which dig
dig @192.168.0.250 google.com

# Check SSH
ssh debian@192.168.0.231 echo OK

# Check Ansible
which ansible-playbook
ansible --version
```

### DNS Stress Test Fails
```bash
# Check DNS server
dig @192.168.0.250 google.com

# Check VIP assignment
ssh debian@192.168.0.231 ip addr show | grep 192.168.0.250

# Check dnsmasq status
ssh debian@192.168.0.231 sudo systemctl status dnsmasq
```

### Failover Test Fails
```bash
# Check Ansible syntax
ansible-playbook --syntax-check keepalived-failover.yml

# Run with verbose output
ansible-playbook -vvv keepalived-failover.yml

# Check keepalived status
ssh debian@192.168.0.231 sudo systemctl status keepalived
ssh debian@192.168.0.232 sudo systemctl status keepalived

# Check priorities
for ip in 192.168.0.231 192.168.0.232 192.168.0.233; do
  echo "=== $ip ==="
  ssh debian@$ip grep priority /etc/keepalived/keepalived.conf
done
```

## Test Scenarios

### Scenario 1: Verify High Availability
```bash
./run-all-tests.sh
```
Confirms the cluster can handle load and survives master failure.

### Scenario 2: Performance Baseline
```bash
./dns-stress-test.sh --queries 100
```
Establishes baseline query throughput and latency.

### Scenario 3: High Load Testing
```bash
./dns-stress-test.sh --queries 1000 --threads 20
```
Tests behavior under peak load (1000 queries, 20 concurrent).

### Scenario 4: Domain-Specific Testing
```bash
./dns-stress-test.sh --domain dns01.ad.alshowto.com --queries 500
```
Tests specific domain performance with 500 queries.

## Monitoring During Tests

### In Another Terminal
```bash
# Watch VIP assignment
watch "ip addr show | grep 192.168.0.250"

# Watch keepalived status
watch "for ip in 192.168.0.231 192.168.0.232 192.168.0.233; do echo \$ip: \$(ssh debian@\$ip sudo systemctl is-active keepalived); done"

# Watch dnsmasq queries
ssh debian@192.168.0.231 tail -f /var/log/dnsmasq/dnsmasq.log
```

## Continuous Integration

### GitHub Actions Example
```yaml
- name: Run DNS Cluster Tests
  run: |
    cd tests
    ./run-all-tests.sh --stress --queries 500
```

### Jenkins Pipeline Example
```groovy
stage('Test') {
  steps {
    sh 'cd tests && ./run-all-tests.sh'
  }
}
```

## Test Logs

Tests generate detailed output to stdout. For permanent logging:

```bash
# Save test results
./run-all-tests.sh | tee test-results-$(date +%Y%m%d-%H%M%S).log

# View logs
grep "PASSED\|FAILED" test-results-*.log
```

## Performance Benchmarks

Expected results on your infrastructure:

| Test | Metric | Expected | Acceptable |
|------|--------|----------|-----------|
| DNS Stress (100q) | QPS | 100+ | 50+ |
| DNS Stress (100q) | Success % | 99.5%+ | 95%+ |
| Failover Time | VIP handoff | < 3s | < 10s |
| Failover Time | DNS available | < 5s | < 15s |
| Recovery Time | MASTER elected | < 2s | < 5s |
| Recovery Time | Service ready | < 5s | < 10s |

## Support

For issues or questions:

1. Check test output for error messages
2. Run individual tests to isolate issues
3. Review troubleshooting section above
4. Check DNS server logs: `/var/log/dnsmasq/dnsmasq.log`
5. Check keepalived logs: `journalctl -u keepalived -f`
