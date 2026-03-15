#!/bin/bash
# DNS and Keepalived Test Suite Runner
# Comprehensive testing of DNS cluster failover and stress testing

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

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
}

warning() {
    echo -e "${YELLOW}⚠ $*${NC}"
}

info() {
    echo -e "${CYAN}ℹ $*${NC}"
}

section() {
    echo ""
    echo "╔════════════════════════════════════════════════════════════╗"
    echo "║ $1"
    echo "╚════════════════════════════════════════════════════════════╝"
    echo ""
}

usage() {
    cat << EOF
Usage: $0 [OPTIONS]

Run comprehensive DNS and keepalived cluster tests

Options:
    -a, --all                   Run all tests (default)
    -s, --stress                Run only DNS stress test
    -f, --failover              Run only keepalived failover test
    -v, --vip VIP               VIP address (default: 192.168.0.250)
    -q, --queries NUM           Stress test queries (default: 100)
    -t, --threads NUM           Stress test threads (default: 10)
    --no-failover-recovery      Skip automatic recovery after failover test
    -h, --help                  Show this help message

Examples:
    $0                          # Run all tests
    $0 --stress --queries 500   # Run stress test with 500 queries
    $0 --failover               # Run only failover test

EOF
    exit 0
}

# Parse arguments
VIP="192.168.0.250"
QUERIES="100"
THREADS="10"
RUN_STRESS=true
RUN_FAILOVER=true
SKIP_RECOVERY=false

while [[ $# -gt 0 ]]; do
    case $1 in
        -a|--all)
            RUN_STRESS=true
            RUN_FAILOVER=true
            shift
            ;;
        -s|--stress)
            RUN_STRESS=true
            RUN_FAILOVER=false
            shift
            ;;
        -f|--failover)
            RUN_STRESS=false
            RUN_FAILOVER=true
            shift
            ;;
        -v|--vip)
            VIP="$2"
            shift 2
            ;;
        -q|--queries)
            QUERIES="$2"
            shift 2
            ;;
        -t|--threads)
            THREADS="$2"
            shift 2
            ;;
        --no-failover-recovery)
            SKIP_RECOVERY=true
            shift
            ;;
        -h|--help)
            usage
            ;;
        *)
            error "Unknown option: $1"
            usage
            ;;
    esac
done

# Check prerequisites
check_prerequisites() {
    section "Checking Prerequisites"

    # Check dig
    if ! command -v dig &> /dev/null; then
        error "dig not found. Install with: apt-get install dnsutils"
        exit 1
    fi
    success "dig is available"

    # Check ansible (for failover test)
    if [ "$RUN_FAILOVER" = true ]; then
        if ! command -v ansible-playbook &> /dev/null; then
            warning "ansible-playbook not found"
            warning "Failover test requires Ansible. Install with: pip install ansible"
            warning "Skipping failover test..."
            RUN_FAILOVER=false
        else
            success "ansible-playbook is available"
        fi
    fi

    # Check SSH connectivity
    info "Checking SSH connectivity to DNS servers..."
    for server in 192.168.0.231 192.168.0.232 192.168.0.233; do
        if ssh -o ConnectTimeout=5 debian@$server "echo OK" > /dev/null 2>&1; then
            success "SSH to $server: OK"
        else
            error "Cannot connect to $server via SSH"
            exit 1
        fi
    done

    # Check DNS server connectivity
    info "Checking DNS server connectivity..."
    if timeout 5 dig @$VIP google.com &> /dev/null; then
        success "DNS server at $VIP is responding"
    else
        error "Cannot reach DNS server at $VIP"
        exit 1
    fi
}

# Run DNS stress test
run_stress_test() {
    section "DNS STRESS TEST"

    if [ ! -f "$SCRIPT_DIR/dns-stress-test.sh" ]; then
        error "dns-stress-test.sh not found"
        exit 1
    fi

    log "Running DNS stress test..."
    log "  VIP: $VIP"
    log "  Queries: $QUERIES per domain"
    log "  Threads: $THREADS concurrent"
    echo ""

    if bash "$SCRIPT_DIR/dns-stress-test.sh" \
        --vip "$VIP" \
        --queries "$QUERIES" \
        --threads "$THREADS"; then
        success "DNS stress test passed"
        STRESS_PASSED=true
    else
        error "DNS stress test failed"
        STRESS_PASSED=false
    fi
}

# Run keepalived failover test
run_failover_test() {
    section "KEEPALIVED FAILOVER TEST"

    if [ ! -f "$SCRIPT_DIR/keepalived-failover.yml" ]; then
        error "keepalived-failover.yml not found"
        exit 1
    fi

    if [ "$RUN_FAILOVER" = false ]; then
        warning "Skipping failover test (Ansible not available)"
        FAILOVER_PASSED=false
        return
    fi

    log "Running keepalived failover test..."
    log "  This will temporarily stop keepalived on dns01 (master)"
    log "  Test will verify failover to dns02 and recovery"
    echo ""

    if cd "$SCRIPT_DIR" && \
       ansible-playbook \
           -i "localhost," \
           --connection=local \
           -v keepalived-failover.yml 2>&1 | grep -q "ok="; then
        success "Keepalived failover test passed"
        FAILOVER_PASSED=true
    else
        error "Keepalived failover test failed"
        FAILOVER_PASSED=false
    fi
}

# Display final summary
display_summary() {
    section "TEST SUMMARY"

    echo "Test Results:"
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

    if [ "$RUN_STRESS" = true ]; then
        if [ "$STRESS_PASSED" = true ]; then
            echo -e "  ${GREEN}✓${NC} DNS Stress Test:          PASSED"
        else
            echo -e "  ${RED}✗${NC} DNS Stress Test:          FAILED"
        fi
    fi

    if [ "$RUN_FAILOVER" = true ]; then
        if [ "$FAILOVER_PASSED" = true ]; then
            echo -e "  ${GREEN}✓${NC} Keepalived Failover Test: PASSED"
        else
            echo -e "  ${RED}✗${NC} Keepalived Failover Test: FAILED"
        fi
    fi

    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo ""

    # Overall result
    ALL_PASSED=true
    [ "$RUN_STRESS" = true ] && [ "$STRESS_PASSED" = false ] && ALL_PASSED=false
    [ "$RUN_FAILOVER" = true ] && [ "$FAILOVER_PASSED" = false ] && ALL_PASSED=false

    if [ "$ALL_PASSED" = true ]; then
        echo -e "${GREEN}"
        cat << 'EOF'
╔═══════════════════════════════════════════════════════════════════╗
║                   ALL TESTS PASSED! ✓                           ║
║                                                                   ║
║  Your DNS cluster is working correctly:                          ║
║  ✓ High performance under load                                   ║
║  ✓ Automatic failover working                                    ║
║  ✓ Service continuity maintained                                 ║
╚═══════════════════════════════════════════════════════════════════╝
EOF
        echo -e "${NC}"
        return 0
    else
        echo -e "${RED}"
        cat << 'EOF'
╔═══════════════════════════════════════════════════════════════════╗
║               SOME TESTS FAILED! ✗                              ║
║                                                                   ║
║  Please review the output above for details                      ║
║  Check /var/log/dnsmasq.log and systemctl status keepalived     ║
╚═══════════════════════════════════════════════════════════════════╝
EOF
        echo -e "${NC}"
        return 1
    fi
}

# Main execution
main() {
    echo ""
    echo -e "${CYAN}"
    cat << 'EOF'
╔═══════════════════════════════════════════════════════════════════╗
║     DNS & Keepalived Cluster Comprehensive Test Suite            ║
╚═══════════════════════════════════════════════════════════════════╝
EOF
    echo -e "${NC}"

    check_prerequisites

    if [ "$RUN_STRESS" = true ]; then
        run_stress_test
    fi

    if [ "$RUN_FAILOVER" = true ]; then
        run_failover_test
    fi

    display_summary
}

# Run main function
main "$@"
