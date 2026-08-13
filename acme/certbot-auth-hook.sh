#!/usr/bin/env bash
# certbot --manual-auth-hook for dnsmasq-ui DNS-01 challenges.
# Pairs with certbot-cleanup-hook.sh.
#
# Usage:
#   1. In the dnsmasq-ui dashboard: Config page -> ACME Hook Keys ->
#      Generate. Copy the key shown -- it's shown exactly once and only
#      its hash is ever stored, so a lost key means generating a new one.
#   2. export DNSMASQUI_URL="http://192.168.0.230:5000"
#      export DNSMASQUI_TOKEN="<the key from step 1>"
#      certbot certonly --manual --preferred-challenges dns \
#        --manual-auth-hook /path/to/certbot-auth-hook.sh \
#        --manual-cleanup-hook /path/to/certbot-cleanup-hook.sh \
#        -d example.ad.alshowto.com
#   3. If this key is ever compromised or no longer needed, revoke it from
#      the same Config page section -- it stops working on its next call.
set -euo pipefail

: "${DNSMASQUI_URL:?DNSMASQUI_URL not set}"
: "${DNSMASQUI_TOKEN:?DNSMASQUI_TOKEN not set}"
: "${CERTBOT_DOMAIN:?certbot did not set CERTBOT_DOMAIN}"
: "${CERTBOT_VALIDATION:?certbot did not set CERTBOT_VALIDATION}"

fulldomain="_acme-challenge.${CERTBOT_DOMAIN}"

response_file=$(mktemp)
http_code=$(curl -s -o "$response_file" -w '%{http_code}' \
  -X POST "${DNSMASQUI_URL}/api/acme-challenge" \
  -H "Authorization: Bearer ${DNSMASQUI_TOKEN}" \
  -H "Content-Type: application/json" \
  -d "{\"fulldomain\":\"${fulldomain}\",\"value\":\"${CERTBOT_VALIDATION}\"}")
body=$(cat "$response_file")
rm -f "$response_file"

if [ "$http_code" != "200" ]; then
  echo "dnsmasq-ui returned HTTP $http_code: $body" >&2
  exit 1
fi

# The API call above already blocks until dnsmasq-ui's deploy_to_servers()
# finishes, but certbot's manual plugin -- unlike acme.sh -- doesn't poll
# public resolvers for propagation itself, so give them a moment too.
sleep 10
