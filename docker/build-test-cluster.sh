#!/bin/bash
# Build and start the dnsmasq-ui test cluster with Docker

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

echo "==================================================================="
echo "dnsmasq-ui Docker Test Cluster"
echo "==================================================================="
echo ""
echo "Building DNS node image..."
docker compose -f "$SCRIPT_DIR/dns-cluster.yml" build

echo ""
echo "Starting test cluster (dns01, dns02, dns03)..."
docker compose -f "$SCRIPT_DIR/dns-cluster.yml" up -d

echo ""
echo "Waiting for containers to be ready..."
sleep 5

echo ""
echo "==================================================================="
echo "Test Cluster Running"
echo "==================================================================="
docker compose -f "$SCRIPT_DIR/dns-cluster.yml" ps

echo ""
echo "Network: dnsmasq-net (172.20.0.0/24)"
echo ""
echo "Servers:"
echo "  dns01: 172.20.0.231 (SSH: ssh root@172.20.0.231)"
echo "  dns02: 172.20.0.232 (SSH: ssh root@172.20.0.232)"
echo "  dns03: 172.20.0.233 (SSH: ssh root@172.20.0.233)"
echo "  VIP:   172.20.0.250 (Keepalived managed)"
echo ""
echo "DNS Queries:"
echo "  dig @172.20.0.250 example.com"
echo ""
echo "To stop the cluster:"
echo "  docker compose -f $SCRIPT_DIR/dns-cluster.yml down"
echo ""
echo "To view logs:"
echo "  docker logs dns01"
echo "  docker logs dns02"
echo "  docker logs dns03"
echo ""
