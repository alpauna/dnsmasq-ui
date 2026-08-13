#!/usr/bin/env bash
# acme.sh custom DNS API hook for dnsmasq-ui.
#
# Install: copy into ~/.acme.sh/dnsapi/dns_dnsmasqui.sh
#
# Usage:
#   1. In the dnsmasq-ui dashboard: Config page -> ACME Hook Keys ->
#      Generate. Copy the key shown -- it's shown exactly once and only
#      its hash is ever stored, so a lost key means generating a new one.
#   2. export DNSMASQUI_URL="http://192.168.0.230:5000"
#      export DNSMASQUI_TOKEN="<the key from step 1>"
#      acme.sh --issue -d example.ad.alshowto.com --dns dns_dnsmasqui
#   3. If this key is ever compromised or no longer needed, revoke it from
#      the same Config page section -- it stops working on its next call.
#
# acme.sh sources this file and calls dns_dnsmasqui_add/_rm itself -- it's
# not meant to be run standalone. _info/_err are acme.sh's own logging
# functions, available in that context.

dns_dnsmasqui_add() {
  fulldomain="$1"
  txtvalue="$2"
  _info "dns_dnsmasqui_add: $fulldomain -> $txtvalue"
  _dnsmasqui_call POST "$fulldomain" "$txtvalue"
}

dns_dnsmasqui_rm() {
  fulldomain="$1"
  txtvalue="$2"
  _info "dns_dnsmasqui_rm: $fulldomain -> $txtvalue"
  _dnsmasqui_call DELETE "$fulldomain" "$txtvalue"
}

_dnsmasqui_call() {
  method="$1"
  fulldomain="$2"
  txtvalue="$3"

  DNSMASQUI_URL="${DNSMASQUI_URL:-}"
  DNSMASQUI_TOKEN="${DNSMASQUI_TOKEN:-}"
  if [ -z "$DNSMASQUI_URL" ] || [ -z "$DNSMASQUI_TOKEN" ]; then
    _err "DNSMASQUI_URL and DNSMASQUI_TOKEN must both be exported before calling acme.sh"
    return 1
  fi

  response_file=$(mktemp)
  http_code=$(curl -s -o "$response_file" -w '%{http_code}' \
    -X "$method" "${DNSMASQUI_URL}/api/acme-challenge" \
    -H "Authorization: Bearer ${DNSMASQUI_TOKEN}" \
    -H "Content-Type: application/json" \
    -d "{\"fulldomain\":\"${fulldomain}\",\"value\":\"${txtvalue}\"}")
  body=$(cat "$response_file")
  rm -f "$response_file"

  if [ "$http_code" != "200" ]; then
    _err "dnsmasq-ui returned HTTP $http_code: $body"
    return 1
  fi
  return 0
}
