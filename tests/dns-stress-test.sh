#!/bin/bash
# DNS Stress Test Script
# Tests DNS performance and reliability under load

set -e

# Configuration
VIP="192.168.0.250"
DNS_QUERIES=100
CONCURRENT_THREADS=10
TIMEOUT=5

# Test domains from dnsmasq config
TEST_DOMAINS=(
    "dns01.ad.alshowto.com"
    "dns02.ad.alshowto.com"
    "dns03.ad.alshowto.com"
    "middle-01.ad.alshowto.com"
)

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

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

usage() {
    cat << EOF
Usage: $0 [OPTIONS]

DNS Stress Test - Tests DNS performance and reliability

Options:
    -v, --vip VIP              VIP address to test (default: 192.168.0.250)
    -q, --queries NUM          Number of queries per domain (default: 100)
    -t, --threads NUM          Concurrent threads (default: 10)
    -d, --domain DOMAIN        Test specific domain (default: all)
    -h, --help                 Show this help message

Examples:
    $0                                    # Default test (100 queries, 10 threads)
    $0 --queries 1000 --threads 20       # High stress (1000 queries, 20 threads)
    $0 --domain dns01.ad.alshowto.com    # Test specific domain

EOF
    exit 0
}

# Parse arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        -v|--vip)
            VIP="$2"
            shift 2
            ;;
        -q|--queries)
            DNS_QUERIES="$2"
            shift 2
            ;;
        -t|--threads)
            CONCURRENT_THREADS="$2"
            shift 2
            ;;
        -d|--domain)
            TEST_DOMAINS=("$2")
            shift 2
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

# Verify DNS tools
if ! command -v dig &> /dev/null; then
    error "dig not found. Install with: apt-get install dnsutils"
    exit 1
fi

# Test connectivity
log "Testing connectivity to DNS server at $VIP..."
if ! timeout $TIMEOUT dig @$VIP google.com &> /dev/null; then
    error "Cannot reach DNS server at $VIP"
    exit 1
fi
success "DNS server is reachable"
echo ""

# Display test parameters
log "Starting DNS Stress Test"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "VIP Address:        $VIP"
echo "Queries per domain: $DNS_QUERIES"
echo "Concurrent threads: $CONCURRENT_THREADS"
echo "Test domains:       ${#TEST_DOMAINS[@]}"
echo "Total queries:      $((${#TEST_DOMAINS[@]} * DNS_QUERIES))"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""

# Initialize counters
TOTAL_QUERIES=0
SUCCESS_QUERIES=0
FAILED_QUERIES=0
TIMEOUTS=0
TOTAL_TIME=0

# Start test
START_TIME=$(date +%s%N)

# Function to test a domain
test_domain_query() {
    local domain=$1
    local query_num=$2

    result=$(timeout $TIMEOUT dig @$VIP $domain +short 2>&1)
    exit_code=$?

    if [ $exit_code -eq 124 ]; then
        echo "TIMEOUT"
        return 1
    elif [ $exit_code -ne 0 ]; then
        echo "FAILED"
        return 2
    elif [ -z "$result" ]; then
        echo "NO_ANSWER"
        return 3
    else
        echo "OK"
        return 0
    fi
}

# Run queries with concurrent threads
run_concurrent_queries() {
    local domain=$1
    local max_pid=0

    log "Testing domain: $domain (${DNS_QUERIES} queries, ${CONCURRENT_THREADS} threads)"

    for ((i=1; i<=DNS_QUERIES; i++)); do
        # Manage concurrent threads
        while [ $(jobs -r -p | wc -l) -ge $CONCURRENT_THREADS ]; do
            sleep 0.01
        done

        # Run query in background
        (
            test_domain_query "$domain" $i
        ) &

        # Track progress every 10 queries
        if [ $((i % 10)) -eq 0 ]; then
            printf "  Progress: %d/%d queries sent\r" $i $DNS_QUERIES
        fi
    done

    # Wait for all background jobs to finish
    wait
    printf "  Progress: %d/%d queries completed\n" $DNS_QUERIES $DNS_QUERIES
}

# Run tests for each domain
echo "Starting queries..."
echo ""

DOMAIN_RESULTS=()

for domain in "${TEST_DOMAINS[@]}"; do
    DOMAIN_START=$(date +%s%N)

    success_count=0
    failed_count=0
    timeout_count=0

    log "Testing domain: $domain"

    for ((i=1; i<=DNS_QUERIES; i++)); do
        result=$(test_domain_query "$domain" $i 2>&1)
        status=$?

        case $status in
            0)
                ((success_count++))
                ((SUCCESS_QUERIES++))
                ;;
            1)
                ((timeout_count++))
                ((TIMEOUTS++))
                ;;
            *)
                ((failed_count++))
                ((FAILED_QUERIES++))
                ;;
        esac

        ((TOTAL_QUERIES++))

        # Show progress
        if [ $((i % 25)) -eq 0 ]; then
            printf "  Progress: %d/%d (✓:%d ✗:%d ⏱:%d)\r" $i $DNS_QUERIES $success_count $failed_count $timeout_count
        fi
    done

    DOMAIN_END=$(date +%s%N)
    DOMAIN_TIME=$(( (DOMAIN_END - DOMAIN_START) / 1000000 ))

    success_rate=$(awk "BEGIN {printf \"%.1f\", ($success_count / $DNS_QUERIES) * 100}")

    if [ "${success_rate%.*}" -ge 95 ]; then
        echo -e "  ${GREEN}✓${NC} $domain: ${GREEN}${success_count}/${DNS_QUERIES}${NC} successful (${success_rate}%) - ${DOMAIN_TIME}ms"
    else
        echo -e "  ${RED}✗${NC} $domain: ${RED}${success_count}/${DNS_QUERIES}${NC} successful (${success_rate}%) - ${DOMAIN_TIME}ms"
    fi

    if [ $failed_count -gt 0 ]; then
        warning "  $domain: $failed_count failed queries"
    fi
    if [ $timeout_count -gt 0 ]; then
        warning "  $domain: $timeout_count timeouts"
    fi

    DOMAIN_RESULTS+=("$domain:$success_count:$failed_count:$timeout_count:$DOMAIN_TIME")
done

END_TIME=$(date +%s%N)
TOTAL_TIME=$(( (END_TIME - START_TIME) / 1000000 ))

# Calculate results
OVERALL_SUCCESS_RATE=$(awk "BEGIN {printf \"%.1f\", ($SUCCESS_QUERIES / $TOTAL_QUERIES) * 100}")
QPS=$(awk "BEGIN {printf \"%.0f\", ($TOTAL_QUERIES * 1000) / $TOTAL_TIME}")

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "TEST RESULTS"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""

# Summary
if [ "${OVERALL_SUCCESS_RATE%.*}" -ge 99 ]; then
    echo -e "Overall Success Rate:  ${GREEN}${OVERALL_SUCCESS_RATE}%${NC} (${SUCCESS_QUERIES}/${TOTAL_QUERIES})"
elif [ "${OVERALL_SUCCESS_RATE%.*}" -ge 95 ]; then
    echo -e "Overall Success Rate:  ${YELLOW}${OVERALL_SUCCESS_RATE}%${NC} (${SUCCESS_QUERIES}/${TOTAL_QUERIES})"
else
    echo -e "Overall Success Rate:  ${RED}${OVERALL_SUCCESS_RATE}%${NC} (${SUCCESS_QUERIES}/${TOTAL_QUERIES})"
fi

echo "Failed Queries:        $FAILED_QUERIES"
echo "Timeouts:              $TIMEOUTS"
echo "Total Test Time:       ${TOTAL_TIME}ms"
echo "Throughput:            ${QPS} QPS (queries per second)"
echo ""
echo "Per-Domain Results:"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

for result in "${DOMAIN_RESULTS[@]}"; do
    IFS=':' read -r domain success failed timeouts time <<< "$result"
    success_rate=$(awk "BEGIN {printf \"%.1f\", ($success / $DNS_QUERIES) * 100}")

    printf "%-30s %5d/%-5d (%5.1f%%) [Failed: %d, Timeouts: %d, Time: %dms]\n" \
        "$domain" "$success" "$DNS_QUERIES" "$success_rate" "$failed" "$timeouts" "$time"
done

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""

# Test verdict
if [ "${OVERALL_SUCCESS_RATE%.*}" -ge 99 ] && [ $TIMEOUTS -eq 0 ]; then
    success "DNS STRESS TEST PASSED - Excellent performance"
    exit 0
elif [ "${OVERALL_SUCCESS_RATE%.*}" -ge 95 ]; then
    warning "DNS STRESS TEST PASSED - With minor issues"
    exit 0
else
    error "DNS STRESS TEST FAILED - Success rate below acceptable threshold"
    exit 1
fi
