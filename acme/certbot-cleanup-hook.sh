#!/usr/bin/env bash
# certbot --manual-cleanup-hook for dnsmasq-ui DNS-01 challenges.
# Pairs with certbot-auth-hook.sh -- see that file for setup instructions.
set -euo pipefail

: "${DNSMASQUI_URL:?DNSMASQUI_URL not set}"
: "${DNSMASQUI_TOKEN:?DNSMASQUI_TOKEN not set}"
: "${CERTBOT_DOMAIN:?certbot did not set CERTBOT_DOMAIN}"
: "${CERTBOT_VALIDATION:?certbot did not set CERTBOT_VALIDATION}"

fulldomain="_acme-challenge.${CERTBOT_DOMAIN}"

# Best-effort: a failed cleanup shouldn't fail the whole certbot run --
# there's no better state to fall back to, and a stray leftover TXT
# record is harmless (just noise) versus certbot exiting non-zero after
# the cert was already issued successfully.
curl -s -o /dev/null \
  -X DELETE "${DNSMASQUI_URL}/api/acme-challenge" \
  -H "Authorization: Bearer ${DNSMASQUI_TOKEN}" \
  -H "Content-Type: application/json" \
  -d "{\"fulldomain\":\"${fulldomain}\",\"value\":\"${CERTBOT_VALIDATION}\"}" || true
