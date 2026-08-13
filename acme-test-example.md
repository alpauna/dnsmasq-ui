# ACME DNS-01 End-to-End Test

A reproducible walkthrough for verifying the [ACME DNS-01 Challenges](README.md#acme-dns-01-challenges-acme_hook_keys--acme_dns_backend)
feature against a real domain, using a throwaway `acme.sh` install and
Let's Encrypt's **staging** environment. This is exactly what was run to
verify the feature after deploying it — see "What went wrong the first
time" below before running this for real.

**Always use `--staging` here.** A staging cert isn't publicly trusted,
so it doesn't consume Let's Encrypt's production rate limits or add a
real cert to public Certificate Transparency logs for a domain that's
just being used as a test target. Only drop `--staging` if you actually
need a browser-trusted certificate out of the run.

## 1. Install acme.sh, fully self-contained

Clone the full repo (the installer needs `dnsapi/`, `notify/`, etc.
alongside the script — fetching just `acme.sh` on its own fails), and
install it entirely under one throwaway directory: no cron job, no shell
profile changes, nothing that outlives `rm -rf` on that directory.

```bash
WORKDIR="$HOME/acme-test"
mkdir -p "$WORKDIR"

git clone --depth 1 https://github.com/acmesh-official/acme.sh.git "$WORKDIR/acme.sh-src"
cd "$WORKDIR/acme.sh-src"

./acme.sh --install \
  --home "$WORKDIR/acme-home" \
  --config-home "$WORKDIR/acme-home/data" \
  --cert-home "$WORKDIR/acme-home/certs" \
  --nocron \
  --noprofile \
  --accountemail "you@example.com"
```

## 2. Install dnsmasq-ui's hook into it

```bash
mkdir -p "$WORKDIR/acme-home/dnsapi"
cp /path/to/dnsmasq-ui/acme/dns_dnsmasqui.sh "$WORKDIR/acme-home/dnsapi/dns_dnsmasqui.sh"
```

## 3. Generate a hook key

Config page → **ACME Hook Keys** → Generate → copy the key (shown exactly
once). Give it an obviously-a-test label so it's easy to find and revoke
afterward, e.g. `acme.sh staging test (delete me)`.

Doing this in the browser is the normal path and the one to reach for —
2FA on the dashboard login makes scripting a `curl`-based login more
trouble than it's worth for a one-off test. If a key needs to be
generated headlessly (no browser available, e.g. scripting this from a
box with only SSH access), it can be done directly against a running
instance instead, bypassing the HTTP API and its session auth entirely:

```bash
# Run on one of the DNS servers, in its venv, against its real zones.json
cd /opt/dnsmasq-ui
export ZONES_CONFIG=/opt/dnsmasq-ui/zones.json
venv/bin/python3 - <<'EOF'
import importlib.util, sys
spec = importlib.util.spec_from_file_location('app_multi_zone', '/opt/dnsmasq-ui/app-multi-zone.py')
amz = importlib.util.module_from_spec(spec)
sys.modules['app_multi_zone'] = amz
spec.loader.exec_module(amz)
key_id, plaintext = amz.manager.create_acme_hook_key('headless test (delete me)')
print(key_id)
print(plaintext)
EOF
```

This calls `create_acme_hook_key()` directly, which still goes through
the normal `save_config()` path (written to disk, pushed to the other two
DNS servers), so a key made this way is revocable the same way as any
other — Config page, or the equivalent one-liner with
`revoke_acme_hook_key(key_id)`.

## 4. Issue against staging

```bash
export DNSMASQUI_URL="http://192.168.0.230:5000"   # the VIP; any of the 3 nodes also works
export DNSMASQUI_TOKEN="<key from step 3>"

"$WORKDIR/acme-home/acme.sh" \
  --home "$WORKDIR/acme-home" \
  --config-home "$WORKDIR/acme-home/data" \
  --cert-home "$WORKDIR/acme-home/certs" \
  --issue \
  --staging \
  -d acme-test.alshowto.com \
  --dns dns_dnsmasqui \
  --debug 1
```

Pick any subdomain of whichever domain `CLOUDFLARE_ZONE_ID` points at
(`acme.env`) — it doesn't need to resolve to anything or have any other
records; DNS-01 only ever checks the `_acme-challenge` TXT name.

## 5. Verify

```bash
# Inspect the issued cert
openssl x509 -in "$WORKDIR/acme-home/certs/acme-test.alshowto.com_ecc/acme-test.alshowto.com.cer" \
  -noout -subject -issuer -dates
# issuer should read "(STAGING) ..." -- if it doesn't, this hit production,
# see "What went wrong" below

# Confirm the challenge TXT record was cleaned up. Check Cloudflare's own
# API, not a public resolver -- a public resolver can still serve a
# cached answer for up to the record's TTL after Cloudflare's authoritative
# copy is already gone, which looks exactly like a leftover but isn't.
cd /opt/dnsmasq-ui   # run on a DNS server, same env as step 3's headless option
set -a && source acme.env && set +a
export ZONES_CONFIG=/opt/dnsmasq-ui/zones.json
venv/bin/python3 - <<'EOF'
import importlib.util, sys
spec = importlib.util.spec_from_file_location('app_multi_zone', '/opt/dnsmasq-ui/app-multi-zone.py')
amz = importlib.util.module_from_spec(spec)
sys.modules['app_multi_zone'] = amz
spec.loader.exec_module(amz)
query = amz.urllib.parse.urlencode({'type': 'TXT', 'name': '_acme-challenge.acme-test.alshowto.com'})
body = amz._cloudflare_request('GET', f'/dns_records?{query}')
print('live Cloudflare records:', body.get('result', []))
EOF
# should print an empty list
```

## 6. Clean up

```bash
# Revoke the test key: Config page -> ACME Hook Keys -> Revoke
# (or venv/bin/python3 -c "...amz.manager.revoke_acme_hook_key('<id>')..."
# the same way it was created, if it was made headlessly)

rm -rf "$WORKDIR"
```

## What went wrong the first time

The first real run of this test passed both `--staging` and
`--server letsencrypt` to acme.sh. `--server` is more specific and wins
when both are given — it silently overrode `--staging` and issued
against **production** Let's Encrypt instead. The run still succeeded
end-to-end (proof the whole chain genuinely works: dnsmasq-ui → Cloudflare
→ Let's Encrypt validation → cert issuance → cleanup), but produced a
real, publicly-trusted certificate for a test hostname (`acme-feature-
test.alshowto.com`) that now exists in public CT logs, instead of a
throwaway staging one.

Not harmful — nothing ever resolved to that hostname and the private key
never left the throwaway `$WORKDIR` before being deleted — but avoidable.
**Don't pass `--server` alongside `--staging`.** If a specific CA
endpoint is ever needed, use `--server` with the *staging* URL directly
(`--server https://acme-staging-v02.api.letsencrypt.org/directory`)
rather than mixing it with the `--staging` shorthand.
