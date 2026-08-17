#!/usr/bin/env python3
"""
dnsmasq-ui v2: Enhanced web UI with multi-zone support.
Manages dnsmasq DNS records across multiple servers and zones.
"""

from flask import Flask, render_template, request, jsonify, redirect, url_for, send_file, session
from flask_cors import CORS
from flask_wtf import CSRFProtect
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.security import generate_password_hash, check_password_hash
import json
import os
import subprocess
import socket
from pathlib import Path
from datetime import datetime, timedelta
import paramiko
import logging
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.primitives import hashes
from cryptography.fernet import Fernet, InvalidToken
import pyotp
import smtplib
from email.mime.text import MIMEText
import hashlib
import base64
import ipaddress
import re
import secrets
import threading
import time
import shlex
import urllib.request
import urllib.parse
import urllib.error
import ssl

app = Flask(__name__)
CORS(app)
csrf = CSRFProtect(app)

# Logging for request tracking
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Configuration
ZONES_FILE = os.getenv('ZONES_CONFIG', 'zones.json')
DNSMASQ_RECORDS_FILE = os.getenv('DNSMASQ_RECORDS_FILE', '/etc/dnsmasq.d/local-records.conf')
# Migration in progress (see ansible/bind9-setup.yml and the migration
# plan): servers move from dnsmasq to BIND9 one at a time, tracked per
# server via servers[name].dns_backend in zones.json rather than a single
# global switch, since there's a real window with both backends live
# across dns01/02/03 simultaneously.
BIND_ZONE_DIR = os.getenv('BIND_ZONE_DIR', '/etc/bind/zones')
SSH_KEY = os.getenv('SSH_KEY', os.path.expanduser('~/.ssh/id_rsa'))
SSH_USER = os.getenv('SSH_USER', 'debian')
# Resolved as an absolute path rather than relying on PATH — the systemd
# unit sets PATH to just the venv's bin dir, which hides /usr/bin/ssh from
# subprocess-based lookups.
SSH_BIN = next((p for p in ('/usr/bin/ssh', '/bin/ssh', '/usr/local/bin/ssh') if os.path.exists(p)), 'ssh')
DOCKER_BIN = next((p for p in ('/usr/bin/docker', '/usr/local/bin/docker') if os.path.exists(p)), 'docker')
SUDO_BIN = next((p for p in ('/usr/bin/sudo', '/bin/sudo') if os.path.exists(p)), 'sudo')
IP_BIN = next((p for p in ('/usr/sbin/ip', '/sbin/ip', '/usr/bin/ip') if os.path.exists(p)), 'ip')
# Not used for reachability gating (see _validate_proxmox_reachability's
# TCP-based check, which replaced ping6 there after it proved
# unreliable) -- only for _verify_subnet_ping_reachability, which uses
# ping6 for exactly the thing it's actually good at: confirming an
# ICMPv6-specific firewall/ipset fix worked, not general reachability.
PING_BIN = next((p for p in ('/usr/bin/ping', '/bin/ping') if os.path.exists(p)), 'ping')
# The service account isn't in the docker group (avoids that standing,
# effectively-root-equivalent grant); reuses the passwordless sudo access
# already relied on elsewhere in this app for remote commands. -n fails
# fast instead of hanging if sudo ever needs a password.
DOCKER_CMD = [SUDO_BIN, '-n', DOCKER_BIN]
WG_KEYS_FILE = os.getenv(
    'WG_KEYS_FILE',
    os.path.join(os.path.dirname(os.path.abspath(ZONES_FILE)), 'wireguard-keys.json')
)
# Device credentials (e.g. enable passwords for switches) — never stored in
# zones.json, which is committed to git. Kept in a separate gitignored file
# with restrictive permissions, same pattern as WG_KEYS_FILE.
DEVICE_CREDENTIALS_FILE = os.getenv(
    'DEVICE_CREDENTIALS_FILE',
    os.path.join(os.path.dirname(os.path.abspath(ZONES_FILE)), 'device-credentials.json')
)
LEGACY_SSH_IMAGE = os.getenv('LEGACY_SSH_IMAGE', 'dnsmasq-ui-legacy-ssh')
# Dashboard login. Single shared admin password (hashed with Werkzeug's
# scrypt-based generate_password_hash) plus a persisted session-signing
# secret — both gitignored, never in zones.json. Set on first run via /setup.
AUTH_FILE = os.getenv(
    'AUTH_FILE',
    os.path.join(os.path.dirname(os.path.abspath(ZONES_FILE)), 'auth.json')
)
LEGACY_SSH_DOCKERFILE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'docker', 'legacy-ssh')
# SMTP relay for email-based 2FA codes. The recipient address is set
# per-account when enabling email 2FA (Configuration page), not here —
# only the relay itself is fixed at deploy time via env vars.
SMTP_SERVER = os.getenv('SMTP_SERVER', '')
SMTP_PORT = int(os.getenv('SMTP_PORT', '587'))
SMTP_USER = os.getenv('SMTP_USER', '')
SMTP_PASSWORD = os.getenv('SMTP_PASSWORD', '')
SMTP_FROM = os.getenv('SMTP_FROM', SMTP_USER)
# Used to build links in emails sent from background contexts (e.g. the
# poller) that have no active Flask request to infer a URL from.
DASHBOARD_URL = os.getenv('DASHBOARD_URL', 'http://192.168.0.233:5000')

# Proxmox VE nodes — used to push a static IPv6 address onto a
# hypervisor's own interface when the subnet it's on drifts (see
# ipv6_host / update_proxmox_interface_v6). Auth is plain root SSH
# (reuses SSH_KEY) rather than the API token in .env, which only has
# Sys.Audit (read), not Sys.Modify — confirmed the hard way. Node names
# map to IPs via two parallel ';'-separated lists (matches .env's
# existing PROXMOX_NODES/PROXMOX_IPS convention for the builder-VM
# scripts) rather than one raw IP, so a node stays correct if it's ever
# readdressed.
PROXMOX_SSH_USER = os.getenv('PROXMOX_SSH_USER', 'root')
PROXMOX_NODE_IPS = dict(zip(
    (n.strip() for n in os.getenv('PROXMOX_NODES', '').split(';') if n.strip()),
    (i.strip() for i in os.getenv('PROXMOX_IPS', '').split(';') if i.strip())
))
# How long a Proxmox node waits, after applying an auto-pushed address
# change, before self-reverting if dnsmasq-ui never confirms it — see
# commit_proxmox_interface_v6. Scheduled via systemd-run on the node
# itself, so this fires even if dnsmasq-ui can't reach the node again.
PROXMOX_COMMIT_TIMEOUT_SECONDS = int(os.getenv('PROXMOX_COMMIT_TIMEOUT_SECONDS', '300'))

# /api/acme-challenge is called by unattended ACME DNS-01 hook scripts
# (acme.sh custom dnsapi, certbot manual-auth-hook) with no dashboard
# session, so the normal login can't gate it. Auth there is per-key bearer
# tokens, generated/revoked from the Config page and stored (hashed, never
# in plaintext) under zones.json global.acme_hook_keys -- see
# ZoneManager.create_acme_hook_key / revoke_acme_hook_key /
# _authenticate_acme_hook_key. No keys configured means every call is
# rejected, same as before this was per-key.
#
# Where the challenge TXT record actually gets published is a separate
# question from that auth, and switchable: Cloudflare currently runs
# alshowto.com's real public DNS (dnsmasq's own zones here are internal/
# split-horizon and never queried by a public CA), so 'cloudflare' is the
# only backend that can make a real Let's Encrypt DNS-01 challenge pass
# today. 'local' -- writing the TXT into zones.json and pushing it to
# dns31/32/33 like any other record -- is kept working for the day
# alshowto.com's authoritative DNS moves onto these servers themselves;
# flipping this one setting is meant to be the entire migration for ACME
# once that happens, no code changes. The hook scripts and /api/acme-
# challenge contract are identical either way -- this is invisible to them.
ACME_DNS_BACKEND = os.getenv('ACME_DNS_BACKEND', 'cloudflare')
CLOUDFLARE_API_TOKEN = os.getenv('CLOUDFLARE_API_TOKEN', '')
CLOUDFLARE_ZONE_ID = os.getenv('CLOUDFLARE_ZONE_ID', '')
CLOUDFLARE_API_BASE = 'https://api.cloudflare.com/client/v4'

def _cloudflare_request(method, path, payload=None):
    """Low-level Cloudflare API call, scoped to the one configured zone.
    Raises on any transport error or a well-formed-but-unsuccessful
    response -- callers translate that into this codebase's usual
    (success, message) tuple convention."""
    if not CLOUDFLARE_API_TOKEN or not CLOUDFLARE_ZONE_ID:
        raise RuntimeError('CLOUDFLARE_API_TOKEN / CLOUDFLARE_ZONE_ID not configured')
    url = f'{CLOUDFLARE_API_BASE}/zones/{CLOUDFLARE_ZONE_ID}{path}'
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(
        url, data=data, method=method,
        headers={
            'Authorization': f'Bearer {CLOUDFLARE_API_TOKEN}',
            'Content-Type': 'application/json',
        }
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        body = json.loads(resp.read().decode())
    if not body.get('success'):
        raise RuntimeError(f"Cloudflare API error: {body.get('errors')}")
    return body

def _cloudflare_add_txt(fulldomain, value):
    try:
        body = _cloudflare_request('POST', '/dns_records', {
            'type': 'TXT', 'name': fulldomain, 'content': value, 'ttl': 120
        })
        return True, f"Created TXT record on Cloudflare (id {body['result']['id']})"
    except Exception as e:
        return False, f"Cloudflare add failed: {e}"

def _cloudflare_remove_txt(fulldomain, value):
    """Looks the record up by (name, content) rather than tracking the
    Cloudflare-assigned id locally -- self-healing across a dnsmasq-ui
    restart between add and remove, no extra state to keep in sync."""
    try:
        query = urllib.parse.urlencode({'type': 'TXT', 'name': fulldomain, 'content': value})
        body = _cloudflare_request('GET', f'/dns_records?{query}')
        matches = body.get('result', [])
        if not matches:
            return True, "No matching Cloudflare TXT record found (already removed?)"
        for record in matches:
            _cloudflare_request('DELETE', f"/dns_records/{record['id']}")
        return True, f"Removed {len(matches)} TXT record(s) from Cloudflare"
    except Exception as e:
        return False, f"Cloudflare removal failed: {e}"

# Reverse proxy configuration
PROXY_PATH_PREFIX = os.getenv('PROXY_PATH_PREFIX', '')  # e.g., '/dnsmasq-ui' for http://proxy/dnsmasq-ui/
TRUSTED_PROXIES = [p.strip() for p in os.getenv('TRUSTED_PROXIES', '*').split(',') if p.strip()]  # comma-separated IPs, or '*'

def _normalize_ip(ip):
    """IPv4-mapped IPv6 ('::ffff:192.168.0.250', what a dual-stack socket
    sees for an IPv4 peer) and its plain IPv4 form should compare equal
    for TRUSTED_PROXIES matching -- otherwise a correctly-configured
    '192.168.0.250' would silently never match."""
    try:
        addr = ipaddress.ip_address(ip)
        return str(addr.ipv4_mapped) if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped else str(addr)
    except ValueError:
        return ip

def _trusted_proxy_fix(wsgi_app):
    """Only apply ProxyFix's X-Forwarded-* trust when the request's
    direct peer is a known reverse proxy (e.g. wherever Pangolin's newt
    agent runs) -- otherwise those headers could be spoofed by anything
    able to reach this app directly (e.g. another host on the LAN), not
    just the actual proxy in front of it. TRUSTED_PROXIES='*' (the
    default) trusts unconditionally, matching this app's behavior before
    this was wired up -- tightening it is opt-in."""
    proxied = ProxyFix(wsgi_app, x_for=1, x_proto=1, x_host=1, x_port=1)
    trust_all = '*' in TRUSTED_PROXIES

    def wrapped(environ, start_response):
        if trust_all or _normalize_ip(environ.get('REMOTE_ADDR', '')) in TRUSTED_PROXIES:
            return proxied(environ, start_response)
        return wsgi_app(environ, start_response)
    return wrapped

def _script_name_prefix(wsgi_app, prefix):
    """Serve the app under a subpath (e.g. a proxy exposing it at
    /dnsmasq-ui/ instead of /) -- an explicit, static override rather
    than trusting an X-Forwarded-Prefix header from the proxy."""
    if not prefix:
        return wsgi_app

    def wrapped(environ, start_response):
        environ['SCRIPT_NAME'] = prefix
        if environ.get('PATH_INFO', '').startswith(prefix):
            environ['PATH_INFO'] = environ['PATH_INFO'][len(prefix):]
        return wsgi_app(environ, start_response)
    return wrapped

app.wsgi_app = _trusted_proxy_fix(_script_name_prefix(app.wsgi_app, PROXY_PATH_PREFIX))

def get_client_ip():
    """Get client IP, respecting X-Forwarded-For from reverse proxy."""
    return request.remote_addr

def _get_local_ips():
    """IPv4 addresses assigned to this host's own interfaces (local only,
    no SSH) — used to tell whether this node currently holds the
    keepalived VIP, and to identify "myself" when syncing state to peers."""
    try:
        result = subprocess.run([IP_BIN, '-4', '-o', 'addr', 'show'], capture_output=True, text=True, timeout=5)
        ips = set()
        for line in result.stdout.splitlines():
            parts = line.split()
            if len(parts) >= 4:
                ips.add(parts[3].split('/')[0])
        return ips
    except Exception as e:
        logger.error(f"Failed to read local IPs: {e}")
        return set()

def _is_local_vrrp_master(vip):
    """Whether this host currently holds the keepalived VIP — i.e. whether
    it's the active node right now, for gating work that shouldn't run
    redundantly on every dnsmasq-ui instance in an HA deployment (e.g. the
    dynamic_hosts poller)."""
    return vip in _get_local_ips()

def _get_local_global_ipv6_prefix():
    """This host's own real, routable IPv6 /64 (as assigned by RA/SLAAC on
    eth0) — not the keepalived VIP, the underlying address the VIP's prefix
    is supposed to track. Used to detect drift between the configured IPv6
    VIP and whatever subnet the active node is actually reachable on (e.g.
    after an ISP renumbers the delegated prefix). Returns None if it can't
    be determined.

    On the current VRRP master, `ip -6 addr show` lists the keepalived VIP
    itself alongside this host's real address, both global-scope on the
    same interface — the VIP shows up with `nodad proto keepalived` (no
    `dynamic` flag), the real RA/SLAAC address always carries `dynamic`.
    Only that one is a meaningful signal here; picking whichever global
    line happens to come first would sometimes just compare the configured
    VIP against itself and silently never detect drift.

    Pinned to `eth0` specifically (the established "primary interface"
    convention used throughout this file, e.g. add_subnet/add_dynamic_host's
    `interface='eth0'` defaults) -- these DNS nodes are also dual-homed
    onto the MGMT VLAN via `eth0.7`, which independently carries its own
    unrelated global-dynamic /64. Without pinning to eth0, whichever of the
    two happened to sort first in `ip addr show`'s output (order isn't
    guaranteed stable across reboots/interface recreation) could get
    compared against the VIP instead, false-alarming a "ISP renumbered"
    drift notice against a subnet the VIP was never meant to track in the
    first place. Confirmed live: both eth0 and eth0.7 show up as
    `scope global dynamic` on all three DNS nodes."""
    try:
        result = subprocess.run([IP_BIN, '-6', '-o', 'addr', 'show', 'dev', 'eth0', 'scope', 'global', 'dynamic'],
                                 capture_output=True, text=True, timeout=5)
        for line in result.stdout.splitlines():
            parts = line.split()
            if len(parts) >= 4 and 'temporary' not in line and 'deprecated' not in line:
                addr = parts[3]
                return ipaddress.ip_interface(addr).network
        return None
    except Exception as e:
        logger.error(f"Failed to read local IPv6 prefix: {e}")
        return None

def _eui64_from_mac(mac):
    """Compute the 64-bit EUI-64 interface identifier SLAAC derives from a
    MAC address (insert ff:fe at the midpoint, flip the universal/local
    bit of the first byte). Returns an int. Raises ValueError for a
    malformed MAC."""
    b = bytes.fromhex(mac.replace(':', '').replace('-', ''))
    if len(b) != 6:
        raise ValueError(f"'{mac}' is not a 6-byte MAC address")
    eui64 = bytes([b[0] ^ 0x02]) + b[1:3] + b'\xff\xfe' + b[3:6]
    return int.from_bytes(eui64, 'big')

def _mac_from_eui64(interface_id):
    """Reverse _eui64_from_mac — recover the original MAC from a 64-bit
    EUI-64 interface identifier (e.g. the host part of an existing SLAAC
    address). Used once per device to bootstrap subnet-tracking from an
    already-known-good address rather than requiring the MAC be looked up
    by hand. Raises ValueError if the identifier doesn't carry the ff:fe
    midpoint EUI-64 requires (e.g. it's a privacy/stable-random address,
    not MAC-derived)."""
    b = interface_id.to_bytes(8, 'big')
    if b[3:5] != b'\xff\xfe':
        raise ValueError("not an EUI-64 interface identifier (missing ff:fe midpoint)")
    mac = bytes([b[0] ^ 0x02]) + b[1:3] + b[5:8]
    return ':'.join(f'{x:02x}' for x in mac)

def _ipv6_from_prefix_and_mac(prefix_net, mac):
    """Combine a subnet's /64 network (an ipaddress.IPv6Network) with a
    device's MAC-derived EUI-64 host bits into its full SLAAC address."""
    return ipaddress.IPv6Address(int(prefix_net.network_address) | _eui64_from_mac(mac))

def _ipv6_from_prefix_and_host(prefix_net, ipv6_host):
    """Combine a subnet's /64 network with an explicit, manually-assigned
    host suffix (e.g. '::11') into its full address -- the static
    counterpart to _ipv6_from_prefix_and_mac, for devices whose address
    isn't SLAAC/EUI-64 derived (e.g. a Proxmox host with a hand-configured
    interface), addressed the same way the IPv6 VIP itself is (see
    README). ipv6_host is parsed as a standalone IPv6Address so its
    integer value *is* the 64 host bits directly, as long as the input
    only used the low 64 bits (e.g. '::11', not a full routable address)."""
    return ipaddress.IPv6Address(int(prefix_net.network_address) | int(ipaddress.IPv6Address(ipv6_host)))

def _ipv4_from_cidr_and_host(cidr, host_number):
    """Combine a subnet's CIDR (e.g. '192.168.0.0/23') with an explicit
    host number into a full IPv4Address, validated to actually fall
    within that network — host numbering only makes unambiguous sense
    once the network/host bit split (/23 vs /24 etc.) is explicit."""
    net = ipaddress.IPv4Network(cidr, strict=False)
    addr = ipaddress.IPv4Address(int(net.network_address) + host_number)
    if addr not in net:
        raise ValueError(f"host {host_number} doesn't fit within {cidr}")
    return addr

def log_request():
    """Log incoming request with client IP and path."""
    client_ip = get_client_ip()
    method = request.method
    path = request.path
    logger.info(f"{client_ip} {method} {path}")

def _load_auth():
    """Load dashboard login config. Returns None if /setup hasn't run yet."""
    if not os.path.exists(AUTH_FILE):
        return None
    try:
        with open(AUTH_FILE) as f:
            return json.load(f)
    except Exception as e:
        logger.error(f"Failed to load auth config: {e}")
        return None

def _save_auth(data):
    fd = os.open(AUTH_FILE, os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w') as f:
        json.dump(data, f, indent=2)
    # 'manager' is a module-level global set up at the bottom of this file;
    # by the time any route handler actually calls _save_auth() at runtime,
    # app startup has already completed and it exists.
    manager._sync_peer_state()

# Session-signing secret: persisted once /setup completes so sessions survive
# restarts. Until then, a per-process random value — any sessions started
# before setup are meaningless anyway (there's no password to have logged in
# with), so losing them on restart is fine.
_auth_config = _load_auth()
app.secret_key = _auth_config['secret_key'] if _auth_config else os.urandom(32).hex()
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SECURE'] = os.getenv('SESSION_COOKIE_SECURE', 'false').lower() == 'true'
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(days=7)

_PUBLIC_PATHS = {
    '/setup', '/login', '/favicon.ico',
    '/login/verify', '/login/verify/totp', '/login/verify/email/send', '/login/verify/email',
    '/api/proxy-check',
    # Exempt from session login, not from auth entirely -- gated by its own
    # bearer-token check (_require_acme_token) since callers are unattended
    # ACME hook scripts with no browser session.
    '/api/acme-challenge'
}

# ACME key-authorization digests are base64url (RFC 4648 sec 5) SHA-256
# hashes -- 43 chars, no padding, alphabet below. Full domain names under
# an _acme-challenge label, bounded to the same length DNS itself allows.
# Both hook scripts (acme.sh, certbot) only ever send well-formed values,
# but this endpoint is reachable by anything holding the bearer token, so
# reject anything that isn't shaped like an actual challenge before it can
# reach save_config()/deploy_to_servers() -- those single-quote the whole
# generated config into a remote shell command (see _ssh_update), so a
# stray quote in unvalidated input would corrupt every zone's deploy, not
# just this record.
_ACME_CHALLENGE_VALUE_RE = re.compile(r'^[A-Za-z0-9_-]{1,128}$')
_ACME_CHALLENGE_DOMAIN_RE = re.compile(
    r'^_acme-challenge\.(?=.{1,253}$)'
    r'[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?'
    r'(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?)+$'
)

def _require_acme_token():
    """Returns an error response if the request's bearer token doesn't
    match any live acme_hook_keys entry, or None if it's authorized to
    proceed. Keys are managed from the Config page (generate/revoke), not
    a single shared secret -- see ZoneManager.create_acme_hook_key."""
    auth_header = request.headers.get('Authorization', '')
    provided = auth_header[7:] if auth_header.startswith('Bearer ') else ''
    if not manager._authenticate_acme_hook_key(provided):
        return jsonify({'error': 'Invalid or missing bearer token'}), 403
    return None

# In-memory only (never persisted): short-lived state for a password-verified
# login awaiting a second factor. Keyed by a random token stored in the
# (unauthenticated-at-this-point) session cookie. Lost on restart, which is
# fine — a half-completed login just has to start over.
_pending_2fa_challenges = {}
_PENDING_2FA_TTL = timedelta(minutes=10)

def _prune_pending_2fa():
    expired = [t for t, c in _pending_2fa_challenges.items()
               if datetime.now() - c['created'] > _PENDING_2FA_TTL]
    for t in expired:
        del _pending_2fa_challenges[t]

def _get_pending_2fa():
    _prune_pending_2fa()
    token = session.get('pending_2fa_token')
    return _pending_2fa_challenges.get(token) if token else None

def _enabled_2fa_methods(auth_config):
    tf = auth_config.get('two_factor', {})
    return [m for m in ('totp', 'email') if tf.get(m, {}).get('enabled')]

def _send_email(to_addr, subject, body):
    if not SMTP_SERVER or not to_addr:
        logger.error(f"Email not sent ('{subject}'): SMTP_SERVER or recipient address not configured")
        return False, "Email delivery isn't configured (SMTP_SERVER not set)"
    try:
        msg = MIMEText(body)
        msg['Subject'] = subject
        msg['From'] = SMTP_FROM
        msg['To'] = to_addr
        with smtplib.SMTP(SMTP_SERVER, SMTP_PORT, timeout=10) as server:
            server.starttls()
            if SMTP_USER:
                server.login(SMTP_USER, SMTP_PASSWORD)
            server.send_message(msg)
        return True, "Sent"
    except Exception as e:
        logger.error(f"Failed to send email ('{subject}') to {to_addr}: {e}")
        return False, f"Failed to send email: {e}"

def _send_email_code(to_addr, code):
    return _send_email(
        to_addr, 'dnsmasq-ui login code',
        f"Your dnsmasq-ui login code is: {code}\n\nThis code expires in 10 minutes."
    )

# Serializes every request against a single global lock — confirmed the
# hard way that Flask's dev server (app.run() at the bottom of this
# file, no threaded=True passed) does NOT actually process requests
# one-at-a-time despite that being the documented default: captured a
# separate client's full request/response cycle completing while a
# slow POST (a Group Update Plan's run_update_group(), which does
# multiple sequential SSH round-trips) was still mid-flight. That
# concurrency, combined with _reload_config_from_disk below replacing
# `manager.config`'s object reference on every request, meant a
# concurrent request's reload could silently orphan a long-running
# request's in-progress mutations — confirmed live: a Group Update
# Plan's lock, set and "saved" after a member failed, was gone by the
# next request because a concurrent reload swapped the config object
# out from under it before the save actually happened against the
# right object. Acquired here, first, and released in teardown_request
# so it covers the full request lifecycle including _reload_config_
# from_disk and every route handler, not just the reload itself.
_request_lock = threading.Lock()

@app.before_request
def _serialize_requests():
    _request_lock.acquire()

@app.teardown_request
def _release_serialize_lock(exception=None):
    if _request_lock.locked():
        _request_lock.release()

@app.before_request
def _require_login():
    if request.path in _PUBLIC_PATHS or request.path.startswith('/static/'):
        return
    auth_config = _load_auth()
    if not auth_config or not auth_config.get('password_hash'):
        if request.path.startswith('/api/'):
            return jsonify({'error': 'Dashboard setup required'}), 401
        return redirect(url_for('setup_page'))
    if not session.get('logged_in'):
        if request.path.startswith('/api/'):
            return jsonify({'error': 'Authentication required'}), 401
        return redirect(url_for('login_page', next=request.path))

@app.route('/setup', methods=['GET', 'POST'])
def setup_page():
    """First-run: set the dashboard's admin password."""
    auth_config = _load_auth()
    if auth_config and auth_config.get('password_hash'):
        return redirect(url_for('login_page'))

    if request.method == 'POST':
        password = request.form.get('password', '')
        confirm = request.form.get('confirm', '')
        if len(password) < 8:
            return render_template('login.html', mode='setup', error='Password must be at least 8 characters')
        if password != confirm:
            return render_template('login.html', mode='setup', error='Passwords do not match')

        secret_key = os.urandom(32).hex()
        _save_auth({'password_hash': generate_password_hash(password), 'secret_key': secret_key})
        app.secret_key = secret_key
        session.permanent = True
        session['logged_in'] = True
        return redirect(url_for('index'))

    return render_template('login.html', mode='setup')

@app.route('/login', methods=['GET', 'POST'])
def login_page():
    auth_config = _load_auth()
    if not auth_config or not auth_config.get('password_hash'):
        return redirect(url_for('setup_page'))

    if request.method == 'POST':
        password = request.form.get('password', '')
        if check_password_hash(auth_config['password_hash'], password):
            methods = _enabled_2fa_methods(auth_config)
            if not methods:
                session.permanent = True
                session['logged_in'] = True
                return redirect(request.args.get('next') or url_for('index'))

            token = secrets.token_urlsafe(24)
            _pending_2fa_challenges[token] = {
                'methods': methods,
                'created': datetime.now(),
                'next': request.args.get('next') or url_for('index')
            }
            session['pending_2fa_token'] = token
            return redirect(url_for('login_verify_page'))
        return render_template('login.html', mode='login', error='Incorrect password')

    return render_template('login.html', mode='login')

@app.route('/login/verify', methods=['GET'])
def login_verify_page():
    challenge = _get_pending_2fa()
    if not challenge:
        return redirect(url_for('login_page'))
    return render_template('login_verify.html', methods=challenge['methods'])

@app.route('/login/verify/totp', methods=['POST'])
def login_verify_totp():
    challenge = _get_pending_2fa()
    if not challenge or 'totp' not in challenge['methods']:
        return redirect(url_for('login_page'))

    auth_config = _load_auth()
    auth_secret = auth_config.get('two_factor', {}).get('totp', {}).get('secret')
    code = request.form.get('code', '').strip()
    if auth_secret and pyotp.TOTP(auth_secret).verify(code, valid_window=1):
        del _pending_2fa_challenges[session.pop('pending_2fa_token')]
        session.permanent = True
        session['logged_in'] = True
        return redirect(challenge['next'])
    return render_template('login_verify.html', methods=challenge['methods'], error='Invalid code')

@app.route('/login/verify/email/send', methods=['POST'])
def login_verify_email_send():
    challenge = _get_pending_2fa()
    if not challenge or 'email' not in challenge['methods']:
        return redirect(url_for('login_page'))

    auth_config = _load_auth()
    to_addr = auth_config.get('two_factor', {}).get('email', {}).get('to')
    code = f"{secrets.randbelow(1000000):06d}"
    challenge['email_code'] = code
    challenge['email_code_expires'] = datetime.now() + timedelta(minutes=10)
    success, message = _send_email_code(to_addr, code)
    return render_template(
        'login_verify.html', methods=challenge['methods'],
        email_sent=success, error=None if success else message
    )

@app.route('/login/verify/email', methods=['POST'])
def login_verify_email_submit():
    challenge = _get_pending_2fa()
    if not challenge or 'email' not in challenge['methods']:
        return redirect(url_for('login_page'))

    code = request.form.get('code', '').strip()
    expires = challenge.get('email_code_expires')
    if challenge.get('email_code') and code == challenge['email_code'] and expires and datetime.now() < expires:
        del _pending_2fa_challenges[session.pop('pending_2fa_token')]
        session.permanent = True
        session['logged_in'] = True
        return redirect(challenge['next'])
    return render_template(
        'login_verify.html', methods=challenge['methods'],
        email_sent=bool(challenge.get('email_code')), error='Invalid or expired code'
    )

@app.route('/logout', methods=['POST'])
def logout():
    session.clear()
    return redirect(url_for('login_page'))

def _linksys_md5_password_transform(password):
    """Replicates the client-side password scrambling in this router's
    login.asp en_value() JS function (not real security, just obscurity —
    a fixed transform with no server-provided nonce). Validated against a
    real Linksys E5400: buffer = password + zero-padded length, then a
    64-char cycle through that buffer, MD5'd."""
    buffer1 = password
    length2 = len(password)
    buffer1 += (f"0{length2}" if length2 < 10 else str(length2))
    length2 += 2
    pseed2 = ''.join(buffer1[p % length2] for p in range(64))
    return hashlib.md5(pseed2.encode()).hexdigest()

# Registry of known web-UI login password transforms, keyed by the name a
# dynamic_hosts entry sets in login_password_transform. Add to this as new
# devices get validated — don't guess a device's scheme blind.
_PASSWORD_TRANSFORMS = {
    'none': lambda pw: pw,
    'linksys_md5': _linksys_md5_password_transform,
}

# BIND9 migration (see ansible/bind9-setup.yml and the migration plan):
# these must match that playbook's `reverse_zones`/`mgmt_zone` vars
# exactly, and the same caveat applies -- verified precisely with
# Python's ipaddress module against the real subnets, not assumed.
# 192.168.0.0/23 (LAN) is two separate /24 reverse zones, not one.
_BIND_REVERSE_ZONES = [
    '0.168.192.in-addr.arpa',
    '1.168.192.in-addr.arpa',
    '7.168.192.in-addr.arpa',
    '8.168.192.in-addr.arpa',  # 192.168.8.0/24, the iot VLAN
    '0.2.1.b.4.0.0.b.0.8.a.4.5.0.6.2.ip6.arpa',
    '0.0.1.c.9.0.0.b.0.8.a.4.5.0.6.2.ip6.arpa',
]
_BIND_MGMT_ZONE = 'mgmt.alshowto.com'
_BIND_MGMT_SIGNING_PRIMARY = 'dns01'

def _bind_absolute(name):
    """BIND zone-file names must be absolute (trailing dot) to avoid
    being silently qualified against $ORIGIN -- this app's stored
    values (hostnames, targets, contacts) never carry one on their own."""
    return name if name.endswith('.') else name + '.'

def _bind_owner(domain, zone_name):
    """The owner-name column for one record line -- always explicit,
    never blank. See generate_bind_zone_file's docstring: a blank/
    omitted owner in a BIND zone file inherits the PREVIOUS record's
    owner, not the zone apex, confirmed empirically in Phase 0 testing
    (two test records silently landed on the wrong name)."""
    if domain == zone_name:
        return '@'
    if domain.endswith('.' + zone_name):
        return domain[:-(len(zone_name) + 1)]
    return _bind_absolute(domain)

class ZoneManager:
    """Manages DNS zones and records."""

    def __init__(self, zones_file):
        self.zones_file = zones_file
        self.config = self._load_config()
        self._vault_key = None  # in-memory only; never persisted to disk
        self._vault_lock_notified = False  # avoid re-emailing every poll cycle for the same lock
        self._v6_vip_drift_notified = False  # avoid re-emailing every poll cycle for the same drift
        # In-memory high-water mark for mgmt.alshowto.com's raw serial --
        # see generate_bind_zone_file. A live SOA query alone races when
        # deploys happen back-to-back (e.g. acme.sh adding two challenge
        # TXT values a few seconds apart): the second deploy's query can
        # observe stale state from before the first deploy's freeze/thaw
        # has settled, so it doesn't know to bump past the first deploy's
        # own new serial. This process's own last-generated value doesn't
        # have that timing dependency.
        self._mgmt_last_serial = None

    def _load_config(self):
        """Load zones and servers configuration."""
        try:
            with open(self.zones_file) as f:
                config = json.load(f)
        except:
            config = {
                'zones': [],
                'servers': {},
                'global': {
                    'upstream_dns': ['1.1.1.1', '8.8.8.8'],
                    'keepalive_vip': '192.168.0.230',
                    'keepalive_interval': 300
                }
            }
        config.setdefault('dynamic_hosts', [])
        return config

    def save_config(self):
        """Save zones configuration to file."""
        with open(self.zones_file, 'w') as f:
            json.dump(self.config, f, indent=2)
        self._sync_peer_state()

    def _sync_peer_state(self):
        """Push zones.json/auth.json/device-credentials.json/smtp.env/
        proxmox.env to the other dnsmasq-ui instances (the other DNS
        servers, in an HA deployment) so a keepalived failover doesn't
        hand off to a peer with stale config, a different session-signing
        secret, a differently keyed vault, or (for proxmox.env) no
        PROXMOX_NODES/IPS to auto-push a drifted address with. Best-effort
        and synchronous — a briefly-unreachable peer logs an error but
        doesn't block the save that triggered this."""
        local_ips = _get_local_ips()
        if not local_ips:
            # Can't reliably tell "myself" apart from a peer right now —
            # syncing anyway would mean pushing to every server in the
            # list, including this one, which self-truncates the file
            # being read (its SFTP write-open races the local read-open on
            # the same path). Skip this round; the next successful save
            # will catch peers up.
            logger.error("_sync_peer_state: could not determine local IPs, skipping sync to avoid self-corruption")
            return
        zones_dir = os.path.dirname(os.path.abspath(self.zones_file))
        files_to_sync = [
            (self.zones_file, 'zones.json', 0o644),
            (AUTH_FILE, 'auth.json', 0o600),
            (DEVICE_CREDENTIALS_FILE, 'device-credentials.json', 0o600),
            (os.path.join(zones_dir, 'smtp.env'), 'smtp.env', 0o600),
            (os.path.join(zones_dir, 'proxmox.env'), 'proxmox.env', 0o600),
            (os.path.join(zones_dir, 'acme.env'), 'acme.env', 0o600),
        ]

        for server_name, server_info in self.get_servers().items():
            ip = server_info.get('ip')
            if not ip or ip in local_ips:
                continue
            try:
                ssh = paramiko.SSHClient()
                ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
                ssh.connect(ip, username=SSH_USER, key_filename=SSH_KEY, timeout=5)
                sftp = ssh.open_sftp()
                for local_path, remote_name, mode in files_to_sync:
                    if os.path.exists(local_path):
                        sftp.put(local_path, f'/opt/dnsmasq-ui/{remote_name}')
                        sftp.chmod(f'/opt/dnsmasq-ui/{remote_name}', mode)
                sftp.close()
                ssh.close()
            except Exception as e:
                logger.error(f"Failed to sync state to peer {server_name} ({ip}): {e}")

    def get_zones(self):
        """Get all zones."""
        return self.config.get('zones', [])

    def get_zone(self, zone_name):
        """Get specific zone."""
        for zone in self.get_zones():
            if zone['name'] == zone_name:
                return zone
        return None

    def add_zone(self, zone_name, description='', zone_type='local'):
        """Add new zone."""
        for zone in self.get_zones():
            if zone['name'] == zone_name:
                return False, "Zone already exists"

        new_zone = {
            'name': zone_name,
            'description': description,
            'type': zone_type,
            'records': []
        }
        self.config['zones'].append(new_zone)
        self.save_config()
        return True, "Zone created"

    def delete_zone(self, zone_name):
        """Delete zone."""
        self.config['zones'] = [z for z in self.config['zones'] if z['name'] != zone_name]
        self.save_config()
        return True, "Zone deleted"

    def add_record(self, zone_name, domain, record_type, value, skip_ptr=False):
        """Add record to zone. Adding an A/AAAA record also creates its
        matching PTR record by default -- see _sync_ptr_record. Pass
        skip_ptr=True to opt a specific record out (e.g. an address this
        app isn't the one who should own reverse DNS for). Every other
        type (including PTR itself) is unaffected regardless."""
        zone = self.get_zone(zone_name)
        if not zone:
            return False, "Zone not found"

        record = {
            'domain': domain,
            'type': record_type,
            'value': value
        }
        zone['records'].append(record)
        if record_type in ('A', 'AAAA') and not skip_ptr:
            self._sync_ptr_record(zone, domain, None, value)
        self.save_config()
        return True, "Record added"

    def update_record(self, zone_name, domain, record_type, new_value, skip_ptr=False):
        """Update record in zone. Changing an A/AAAA record's value moves
        its PTR record to match -- see _sync_ptr_record. skip_ptr=True
        leaves any existing PTR record for the old value untouched
        instead of removing it."""
        zone = self.get_zone(zone_name)
        if not zone:
            return False, "Zone not found"

        for record in zone['records']:
            if record['domain'] == domain and record['type'] == record_type:
                old_value = record['value']
                record['value'] = new_value
                if record_type in ('A', 'AAAA') and not skip_ptr:
                    self._sync_ptr_record(zone, domain, old_value, new_value)
                self.save_config()
                return True, "Record updated"

        return False, "Record not found"

    def delete_record(self, zone_name, domain, record_type):
        """Delete record from zone. Deleting an A/AAAA record also removes
        its PTR record(s) -- see _sync_ptr_record. Unlike dynamic_hosts
        (where "stop tracking" deliberately leaves the last-known forward
        record in place), an explicit delete here is a real deletion, so
        leaving an orphaned PTR behind would be wrong, not conservative."""
        zone = self.get_zone(zone_name)
        if not zone:
            return False, "Zone not found"

        if record_type in ('A', 'AAAA'):
            old_values = [r['value'] for r in zone['records']
                          if r['domain'] == domain and r['type'] == record_type]
            for old_value in old_values:
                self._sync_ptr_record(zone, domain, old_value, None)

        zone['records'] = [r for r in zone['records']
                          if not (r['domain'] == domain and r['type'] == record_type)]
        self.save_config()
        return True, "Record deleted"

    def _zone_for_domain(self, fulldomain):
        """Longest-suffix match against configured zone names, e.g.
        '_acme-challenge.foo.ad.alshowto.com' -> zone 'ad.alshowto.com'.
        Picks the most specific zone if more than one suffix matches."""
        candidates = [z for z in self.get_zones()
                      if fulldomain == z['name'] or fulldomain.endswith('.' + z['name'])]
        if not candidates:
            return None
        return max(candidates, key=lambda z: len(z['name']))

    def add_txt_challenge(self, fulldomain, value):
        """Publish an ACME DNS-01 challenge TXT record. Routes per-domain:
        if fulldomain falls under a zone we host locally (e.g.
        rv-tx.com), use the local backend regardless of
        ACME_DNS_BACKEND -- that global setting only picks the backend
        for domains with NO local zone match (today, that's everything
        under alshowto.com, still Cloudflare-authoritative). Without
        this check, flipping ACME_DNS_BACKEND to 'local' for a new
        self-hosted domain would also silently break every existing
        Cloudflare-backed renewal (e.g. pangolin.alshowto.com's live
        acme.sh hook)."""
        if self._zone_for_domain(fulldomain):
            return self._add_txt_challenge_local(fulldomain, value)
        if ACME_DNS_BACKEND == 'cloudflare':
            return _cloudflare_add_txt(fulldomain, value)
        return self._add_txt_challenge_local(fulldomain, value)

    def remove_txt_challenge(self, fulldomain, value):
        """Remove one specific ACME challenge TXT value. See
        add_txt_challenge for the per-domain routing rationale."""
        if self._zone_for_domain(fulldomain):
            return self._remove_txt_challenge_local(fulldomain, value)
        if ACME_DNS_BACKEND == 'cloudflare':
            return _cloudflare_remove_txt(fulldomain, value)
        return self._remove_txt_challenge_local(fulldomain, value)

    def _add_txt_challenge_local(self, fulldomain, value):
        """Local-zone-file path: writes into zones.json and pushes to
        dns31/32/33 like any other record. Only actually reachable by a
        public CA once alshowto.com's authoritative DNS moves onto these
        servers -- see the ACME_DNS_BACKEND comment above. Deliberately
        does not dedupe/overwrite by domain like add_record's other
        callers assume -- a wildcard + apex cert request needs two TXT
        values live under the same _acme-challenge name at once, so this
        always appends."""
        zone = self._zone_for_domain(fulldomain)
        if not zone:
            return False, f"No zone configured for {fulldomain}"
        zone['records'].append({'domain': fulldomain, 'type': 'TXT', 'value': value})
        self.save_config()
        return True, "TXT challenge record added"

    def _remove_txt_challenge_local(self, fulldomain, value):
        """Local-zone-file counterpart to _add_txt_challenge_local.
        Removes one specific ACME challenge TXT value, not every TXT
        record under that name -- a concurrent wildcard+apex request can
        have two live at once and cleanup must not race the other one."""
        zone = self._zone_for_domain(fulldomain)
        if not zone:
            return False, f"No zone configured for {fulldomain}"
        before = len(zone['records'])
        zone['records'] = [r for r in zone['records']
                            if not (r['domain'] == fulldomain and r['type'] == 'TXT'
                                    and r['value'] == value)]
        removed = before - len(zone['records'])
        self.save_config()
        return True, f"Removed {removed} TXT record(s)"

    def get_acme_hook_keys(self):
        """Metadata only -- key_hash never leaves this method, and the
        plaintext key only ever exists in create_acme_hook_key's return
        value, once."""
        keys = self.config.get('global', {}).get('acme_hook_keys', [])
        return [{k: v for k, v in key.items() if k != 'key_hash'} for key in keys]

    def create_acme_hook_key(self, label):
        """Generates a new /api/acme-challenge bearer token and returns the
        plaintext exactly once. Only its hash is persisted, so losing this
        response means generating a replacement, not recovering it -- same
        UX as a GitHub personal access token."""
        plaintext = secrets.token_urlsafe(32)
        entry = {
            'id': secrets.token_hex(4),
            'label': label or 'unlabeled',
            'key_hash': generate_password_hash(plaintext),
            'created': datetime.now().isoformat(),
            'last_used': None
        }
        self.config.setdefault('global', {}).setdefault('acme_hook_keys', []).append(entry)
        self.save_config()
        return entry['id'], plaintext

    def revoke_acme_hook_key(self, key_id):
        """Deletes a key outright -- revocation should mean a script using
        it starts failing immediately, not just stops being listed."""
        global_cfg = self.config.setdefault('global', {})
        keys = global_cfg.get('acme_hook_keys', [])
        before = len(keys)
        global_cfg['acme_hook_keys'] = [k for k in keys if k['id'] != key_id]
        removed = before - len(global_cfg['acme_hook_keys'])
        if removed:
            self.save_config()
        return removed > 0

    def _authenticate_acme_hook_key(self, provided_token):
        """Hashes are salted, so this can't be a dict lookup by hash --
        an O(n) scan over a handful of hook keys is fine. On a match,
        updates last_used in memory only; the caller's own add/remove
        already calls save_config() right after, so this rides along with
        that write instead of costing its own peer-sync SSH round trip
        just to persist a timestamp."""
        if not provided_token:
            return False
        for key in self.config.get('global', {}).get('acme_hook_keys', []):
            if check_password_hash(key['key_hash'], provided_token):
                key['last_used'] = datetime.now().isoformat()
                return True
        return False

    def generate_dnsmasq_config(self):
        """Generate complete dnsmasq configuration."""
        config = "# Auto-generated by dnsmasq-ui\n"
        config += f"# Generated: {datetime.now().isoformat()}\n\n"

        # Add all records from all zones
        for zone in self.get_zones():
            config += f"# Zone: {zone['name']}\n"
            for record in zone.get('records', []):
                domain = record['domain']
                record_type = record['type']
                value = record['value']

                if record_type == 'CNAME':
                    # value is 'target' or 'target <ttl>' -- unlike MX/SRV/
                    # CAA's always-required extra fields, TTL here is
                    # optional (dnsmasq's cname= supports a trailing TTL,
                    # most of the other directives this app generates
                    # don't support per-record TTL at all). A single-token
                    # value is exactly what every CNAME record already
                    # looked like before TTL support existed, so this is
                    # fully backward compatible with no migration needed.
                    parts = value.split()
                    if len(parts) == 1:
                        config += f"cname={domain},{parts[0]}\n"
                    elif len(parts) == 2 and parts[1].isdigit():
                        config += f"cname={domain},{parts[0]},{parts[1]}\n"
                    else:
                        config += f"# Skipped malformed CNAME record for {domain}: '{value}' (expected 'target' or 'target <ttl>')\n"
                elif record_type == 'TXT':
                    config += f"txt-record={domain},{value}\n"
                elif record_type == 'MX':
                    # value is '<preference> <hostname>' -- MX needs two
                    # fields and the record schema is just {domain, type,
                    # value}, so they're encoded into that one string
                    # rather than widening the schema for every record
                    # type. Malformed values are skipped (as a comment,
                    # not raised) since this loop builds every zone's
                    # config in one pass -- one bad record must never take
                    # down the whole deploy.
                    parts = value.split(None, 1)
                    if len(parts) == 2 and parts[0].isdigit():
                        preference, hostname = parts
                        config += f"mx-host={domain},{hostname},{preference}\n"
                    else:
                        config += f"# Skipped malformed MX record for {domain}: '{value}' (expected '<preference> <hostname>')\n"
                elif record_type == 'SRV':
                    # value is '<target> <port> <priority> <weight>' --
                    # same single-string encoding rationale as MX above.
                    parts = value.split()
                    if len(parts) == 4 and all(p.isdigit() for p in parts[1:]):
                        target, port, priority, weight = parts
                        config += f"srv-host={domain},{target},{port},{priority},{weight}\n"
                    else:
                        config += f"# Skipped malformed SRV record for {domain}: '{value}' (expected '<target> <port> <priority> <weight>')\n"
                elif record_type == 'CAA':
                    # value is '<flags> <tag> <value>' -- same encoding
                    # rationale as MX/SRV above. maxsplit=2 so the CAA
                    # value itself (3rd field) can still contain spaces
                    # (e.g. an iodef contact string), unlike flags/tag.
                    parts = value.split(None, 2)
                    if len(parts) == 3 and parts[0].isdigit():
                        flags, tag, caa_value = parts
                        config += f"caa-record={domain},{flags},{tag},{caa_value}\n"
                    else:
                        config += f"# Skipped malformed CAA record for {domain}: '{value}' (expected '<flags> <tag> <value>')\n"
                elif record_type == 'PTR':
                    # Unlike MX/SRV/CAA, PTR only ever needs the two
                    # fields the schema already has -- domain holds the
                    # reverse-lookup name (<ip>.in-addr.arpa or
                    # <ip>.ip6.arpa), value holds the target hostname --
                    # so no compound-value encoding/parsing needed here.
                    config += f"ptr-record={domain},{value}\n"
                else:
                    config += f"address=/{domain}/{value}\n"
            config += "\n"

        # Local TTL -- dnsmasq answers config-sourced records (address=,
        # txt-record=, mx-host=, srv-host=, caa-record=, ptr-record=, and
        # any cname= with no per-record TTL of its own) with TTL 0 by
        # default, meaning "don't cache, ask again every time". None of
        # those directives except cname= support a per-record TTL, so
        # this global setting is the only lever for any of the others.
        local_ttl = self.config.get('global', {}).get('local_ttl')
        if local_ttl:
            config += f"# Local TTL\nlocal-ttl={local_ttl}\n\n"

        # Add upstream DNS
        upstream = self.config.get('global', {}).get('upstream_dns', ['1.1.1.1', '8.8.8.8'])
        config += "# Upstream DNS\n"
        for dns in upstream:
            config += f"server={dns}\n"

        return config

    def _get_live_zone_serial(self, zone_name):
        """Query dns01 (the mgmt.alshowto.com signing primary) directly
        for a zone's currently-loaded SOA serial. Best-effort: returns
        None on any failure (dig missing, network issue, zone not
        loaded yet on a fresh provision) so callers fall back to their
        own serial scheme rather than blocking a deploy on this."""
        try:
            primary_ip = self.config['servers'][_BIND_MGMT_SIGNING_PRIMARY]['ip']
            result = subprocess.run(
                ['dig', '+short', '+time=3', '+tries=1', 'SOA', zone_name, f'@{primary_ip}'],
                capture_output=True, text=True, timeout=5
            )
            fields = result.stdout.split()
            return int(fields[2]) if len(fields) >= 3 else None
        except Exception:
            return None

    def generate_bind_zone_file(self, zone):
        """Generate a BIND zone file for one forward zone. Reuses the
        exact same MX/SRV/CAA/CNAME value-splitting logic as
        generate_dnsmasq_config(), retargeted to BIND's native zone-file
        syntax -- two things differ from a naive reformat, both found
        empirically in Phase 0 sandbox testing against the real BIND9
        binary (see the migration plan):

        1. SRV's stored field order is dnsmasq's own convention
           ('<target> <port> <priority> <weight>'), but BIND's native
           SRV text order is 'priority weight port target' -- this
           REORDERS, not just reformats, or it would silently produce a
           wrong-but-syntactically-valid record.
        2. Every record gets an explicit owner name ('@' for the zone
           apex) via _bind_owner() -- a blank/omitted owner in a BIND
           zone file inherits the PREVIOUS record's owner, not the zone
           apex (confirmed by two test records silently landing on the
           wrong name when left blank), so this app never omits one for
           brevity.

        PTR records are skipped here entirely -- they don't belong in a
        forward zone at all, see generate_bind_reverse_zone_files()."""
        zone_name = zone['name']
        soa = zone.get('soa') or {}
        ns_hostnames = self.config.get('global', {}).get('ns_hostnames', [])
        primary_ns = soa.get('ns') or (ns_hostnames[0] if ns_hostnames else f'ns1.{zone_name}')
        contact = soa.get('contact', 'admin.alshowto.com')
        if '@' in contact:
            contact = contact.replace('@', '.', 1)
        ttl = soa.get('ttl', 3600)
        # mgmt.alshowto.com is inline-signed with serial-update-method
        # unixtime -- that setting only governs the *signed* side; the
        # RAW zone file still needs its own strictly-increasing serial
        # for ixfr-from-differences to accept a reload. zones.json's
        # static soa.serial would go stale (never bumped); a raw Unix
        # timestamp was tried and is WRONG here too -- the Ansible
        # placeholder's date-coded serial (YYYYMMDDnn, e.g. 2026081300,
        # ~2.03 billion) is already larger than any real Unix timestamp
        # will be until year 2033, so a real timestamp (~1.8 billion in
        # 2026) reads as "backward" under RFC 1982 comparison and gets
        # rejected forever. Fix: keep the SAME YYYYMMDDnn family the
        # placeholder used, with a same-day 15-minute-resolution counter
        # (00-95) so it both stays ahead of that baseline and keeps
        # increasing across repeated same-day deploys.
        #
        # That alone still isn't sufficient, though (confirmed live,
        # 2026-08-13): serial-update-method unixtime governs the
        # *signed* side, but silently falls back to plain incrementing
        # whenever the raw serial doesn't look like a real timestamp --
        # which is every deploy here, by design, per the note above.
        # That means the signed side's serial can advance on its own
        # from automatic re-signing/key-rollover activity alone, with
        # no deploy involved -- a fixed formula computed from wall-clock
        # time alone can silently fall behind and get rejected
        # ("ixfr-from-differences: new serial ... out of range",
        # logged but NOT surfaced as an rndc error -- `rndc thaw`
        # reports success while the load fails asynchronously, so this
        # was invisible to deploy_to_servers()'s caller. First caught it
        # breaking ACME challenge propagation for mgmt.alshowto.com).
        # Only asking BIND what it currently has can reliably stay
        # ahead of that, so this queries the live serial and takes
        # whichever of the two is larger.
        if zone_name == _BIND_MGMT_ZONE:
            now = datetime.now()
            serial = int(now.strftime('%Y%m%d')) * 100 + (now.hour * 4 + now.minute // 15)
            live_serial = self._get_live_zone_serial(zone_name)
            floor = max(live_serial or 0, self._mgmt_last_serial or 0)
            if floor >= serial:
                serial = floor + 1
            self._mgmt_last_serial = serial
        else:
            serial = soa.get('serial') or int(datetime.now().timestamp())

        lines = [
            f"$TTL {ttl}",
            f"@\tIN\tSOA\t{_bind_absolute(primary_ns)} {_bind_absolute(contact)} (",
            f"\t\t\t{serial}\t; serial",
            f"\t\t\t{soa.get('refresh', 3600)}\t\t; refresh",
            f"\t\t\t{soa.get('retry', 1800)}\t\t; retry",
            f"\t\t\t{soa.get('expire', 604800)}\t\t; expire",
            f"\t\t\t{ttl} )\t\t; minimum",
            "",
        ]
        for ns in ns_hostnames:
            lines.append(f"\tIN\tNS\t{_bind_absolute(ns)}")
        lines.append("")

        for record in zone.get('records', []):
            domain = record['domain']
            record_type = record['type']
            value = record['value']
            if record_type == 'PTR':
                continue
            owner = _bind_owner(domain, zone_name)

            if record_type == 'CNAME':
                parts = value.split()
                if len(parts) == 1:
                    lines.append(f"{owner}\tIN\tCNAME\t{_bind_absolute(parts[0])}")
                elif len(parts) == 2 and parts[1].isdigit():
                    lines.append(f"{owner}\t{parts[1]}\tIN\tCNAME\t{_bind_absolute(parts[0])}")
                else:
                    lines.append(f"; Skipped malformed CNAME record for {domain}: '{value}' (expected 'target' or 'target <ttl>')")
            elif record_type == 'TXT':
                lines.append(f'{owner}\tIN\tTXT\t"{value}"')
            elif record_type == 'MX':
                parts = value.split(None, 1)
                if len(parts) == 2 and parts[0].isdigit():
                    preference, hostname = parts
                    lines.append(f"{owner}\tIN\tMX\t{preference} {_bind_absolute(hostname)}")
                else:
                    lines.append(f"; Skipped malformed MX record for {domain}: '{value}' (expected '<preference> <hostname>')")
            elif record_type == 'SRV':
                parts = value.split()
                if len(parts) == 4 and all(p.isdigit() for p in parts[1:]):
                    target, port, priority, weight = parts
                    lines.append(f"{owner}\tIN\tSRV\t{priority} {weight} {port} {_bind_absolute(target)}")
                else:
                    lines.append(f"; Skipped malformed SRV record for {domain}: '{value}' (expected '<target> <port> <priority> <weight>')")
            elif record_type == 'CAA':
                parts = value.split(None, 2)
                if len(parts) == 3 and parts[0].isdigit():
                    flags, tag, caa_value = parts
                    lines.append(f'{owner}\tIN\tCAA\t{flags} {tag} "{caa_value}"')
                else:
                    lines.append(f"; Skipped malformed CAA record for {domain}: '{value}' (expected '<flags> <tag> <value>')")
            elif record_type in ('A', 'AAAA'):
                lines.append(f"{owner}\tIN\t{record_type}\t{value}")
            else:
                lines.append(f"; Skipped unknown record type '{record_type}' for {domain}")

        return "\n".join(lines) + "\n"

    def generate_bind_reverse_zone_files(self):
        """Bucket every PTR record across all zones into its correct
        BIND reverse zone file, keyed by _BIND_REVERSE_ZONES (must match
        ansible/bind9-setup.yml's `reverse_zones` var exactly). Returns
        {reverse_zone_name: file_content}. Correctness beyond "loads
        without error" doesn't matter here -- no reverse zone is
        publicly delegated (the ISP owns real reverse authority for
        these address blocks) -- so a shared default SOA is fine rather
        than needing one per reverse zone in zones.json."""
        ns_hostnames = self.config.get('global', {}).get('ns_hostnames', [])
        primary_ns = ns_hostnames[0] if ns_hostnames else 'ns1.invalid'
        buckets = {z: [] for z in _BIND_REVERSE_ZONES}

        for zone in self.get_zones():
            for record in zone.get('records', []):
                if record['type'] != 'PTR':
                    continue
                domain = record['domain']
                match = next((z for z in _BIND_REVERSE_ZONES
                              if domain == z or domain.endswith('.' + z)), None)
                if not match:
                    continue
                owner = _bind_owner(domain, match)
                buckets[match].append(f"{owner}\tIN\tPTR\t{_bind_absolute(record['value'])}")

        files = {}
        for rzone, ptr_lines in buckets.items():
            lines = [
                "$TTL 3600",
                f"@\tIN\tSOA\t{_bind_absolute(primary_ns)} admin.alshowto.com. (",
                f"\t\t\t{int(datetime.now().timestamp())}\t; serial",
                "\t\t\t3600\t\t; refresh",
                "\t\t\t1800\t\t; retry",
                "\t\t\t604800\t\t; expire",
                "\t\t\t3600 )\t\t; minimum",
                "",
            ]
            for ns in ns_hostnames:
                lines.append(f"\tIN\tNS\t{_bind_absolute(ns)}")
            lines.append("")
            lines.extend(ptr_lines)
            files[rzone] = "\n".join(lines) + "\n"
        return files

    def deploy_to_servers(self):
        """Deploy configuration to all enabled servers. Backend-aware
        per server (servers[name].dns_backend, 'dnsmasq' default or
        'bind9') -- the BIND9 migration cuts servers over one at a time
        (see the migration plan's Phase 3), so there's a real window
        where some servers are still on dnsmasq and others have already
        moved, each needing a completely different push mechanism and
        config format. Don't "simplify" this to a single backend without
        first confirming every server has actually finished migrating
        (check for any remaining dns_backend == 'dnsmasq' entries)."""
        servers = self.config.get('servers', {})
        results = {}

        dnsmasq_needed = any(
            s.get('enabled', True) and s.get('dns_backend', 'dnsmasq') == 'dnsmasq'
            for s in servers.values()
        )
        dnsmasq_config = self.generate_dnsmasq_config() if dnsmasq_needed else None

        bind_needed = any(
            s.get('enabled', True) and s.get('dns_backend', 'dnsmasq') == 'bind9'
            for s in servers.values()
        )
        bind_reverse_files = self.generate_bind_reverse_zone_files() if bind_needed else None

        for server_name, server_info in servers.items():
            if not server_info.get('enabled', True):
                continue

            if server_info.get('dns_backend', 'dnsmasq') == 'bind9':
                results[server_name] = self._deploy_bind_zones(server_name, server_info['ip'], bind_reverse_files)
            else:
                success, message = self._ssh_update(server_info['ip'], dnsmasq_config)
                results[server_name] = {'success': success, 'message': message}

        return results

    def _deploy_bind_zones(self, server_name, server_ip, reverse_files):
        """Push every zone this server should have to it: forward zones
        (all of them, except mgmt.alshowto.com if this server isn't the
        designated signing primary -- it gets that one via BIND's own
        AXFR instead, once NOTIFYed by the primary) plus every reverse
        zone. Each zone is checked with named-checkzone before rndc
        reload -- a bad record aborts just that zone's reload, not the
        whole server's deploy."""
        zone_results = {}
        for zone in self.get_zones():
            if zone['name'] == _BIND_MGMT_ZONE and server_name != _BIND_MGMT_SIGNING_PRIMARY:
                continue
            content = self.generate_bind_zone_file(zone)
            if zone['name'] == _BIND_MGMT_ZONE:
                # Only reached on the signing primary (see the skip
                # above) -- see _deploy_bind_zone's docstring for why
                # this zone specifically needs a view qualifier and
                # freeze/thaw instead of a plain reload.
                zone_results[zone['name']] = self._deploy_bind_zone(
                    server_ip, zone['name'], content, view='public', dynamic=True)
            else:
                zone_results[zone['name']] = self._deploy_bind_zone(server_ip, zone['name'], content)

        for rzone_name, content in reverse_files.items():
            zone_results[rzone_name] = self._deploy_bind_zone(server_ip, rzone_name, content)

        failures = {z: r['message'] for z, r in zone_results.items() if not r['success']}
        if failures:
            return {'success': False, 'message': '; '.join(f"{z}: {msg}" for z, msg in failures.items())}
        return {'success': True, 'message': f"{len(zone_results)} zone(s) updated and reloaded"}

    def _deploy_bind_zone(self, server_ip, zone_name, content, view=None, dynamic=False):
        """Push one zone file to one server: SFTP the content,
        named-checkzone remotely (abort without reloading if it fails --
        a bad record must never take down a zone that was previously
        loading fine), then rndc reload just that zone -- unlike
        dnsmasq, BIND can reload a single zone's data without a full
        restart (see the migration plan's Phase 0 finding on rndc
        reconfig vs. reload vs. restart).

        view/dynamic exist for mgmt.alshowto.com on the signing primary
        only (see the caller): that zone lives in two named views there
        ("public", the real definition, and "internal", an in-view
        reference -- see the split-horizon comment in named.conf.local),
        so a bare `rndc reload <zone>` is ambiguous and fails outright.
        It's also a "dynamic" zone (has update-policy, for RFC2136 ACME
        renewals) -- BIND refuses a plain reload there too, since it
        could silently discard legitimate updates that only exist in the
        zone's journal. Confirmed live (2026-08-13): a wildcard-record
        deploy to this zone silently failed this exact way until this
        was added -- the file uploaded fine, checkzone passed, only the
        reload step errored, so it's easy to miss in a quick glance at
        deploy_to_servers()'s success/failure summary."""
        remote_path = f"{BIND_ZONE_DIR}/db.{zone_name}"
        try:
            self._write_remote_root_file(server_ip, content, remote_path, mode='644', owner='bind', group='bind')
        except Exception as e:
            return {'success': False, 'message': f"upload failed: {e}"}

        ok, output = self._run_remote_root_command(server_ip, f"named-checkzone {zone_name} {remote_path}")
        if not ok:
            return {'success': False, 'message': f"named-checkzone failed, not reloaded: {output}"}

        view_suffix = f" in {view}" if view else ""
        if dynamic:
            ok, output = self._run_remote_root_command(server_ip, f"rndc freeze {zone_name}{view_suffix}")
            if not ok:
                return {'success': False, 'message': f"rndc freeze failed: {output}"}
            ok, output = self._run_remote_root_command(server_ip, f"rndc thaw {zone_name}{view_suffix}")
            if not ok:
                return {'success': False, 'message': f"rndc thaw failed: {output}"}
        else:
            ok, output = self._run_remote_root_command(server_ip, f"rndc reload {zone_name}{view_suffix}")
            if not ok:
                return {'success': False, 'message': f"rndc reload failed: {output}"}

        return {'success': True, 'message': 'updated and reloaded'}

    def _ssh_update(self, server_ip, config_content):
        """Update dnsmasq config via SSH."""
        try:
            ssh = paramiko.SSHClient()
            ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            ssh.connect(server_ip, username=SSH_USER, key_filename=SSH_KEY, timeout=5)

            # Write config and do a full restart. SIGHUP only reloads /etc/hosts-style
            # dynamic data; address=/cname= directives from conf-dir are parsed once
            # at startup and need a real restart to pick up changes. Falls back to
            # pkill+respawn on hosts without systemd (e.g. the Docker dns-node image).
            cmd = (
                f"echo '{config_content}' | sudo tee {DNSMASQ_RECORDS_FILE} > /dev/null && "
                "(sudo systemctl restart dnsmasq 2>/dev/null || "
                "(sudo pkill dnsmasq; sleep 1; sudo /usr/sbin/dnsmasq -C /etc/dnsmasq.conf &))"
            )
            stdin, stdout, stderr = ssh.exec_command(cmd)
            error = stderr.read().decode()
            ssh.close()

            if error:
                return False, error
            return True, "Config updated and dnsmasq restarted"

        except Exception as e:
            return False, str(e)

    def get_servers(self):
        """Get all servers."""
        return self.config.get('servers', {})

    def get_server_ipv6(self, hostname):
        """A server's own currently-published AAAA record (e.g.
        dns01.ad.alshowto.com), for display alongside its IPv4 address —
        reuses whatever the subnet tracker already keeps current rather
        than a fresh SSH round-trip just to show it. Returns None if the
        server has no AAAA record tracked."""
        for zone in self.config.get('zones', []):
            for r in zone.get('records', []):
                if r['type'] == 'AAAA' and r['domain'].startswith(f"{hostname}."):
                    return r['value']
        return None

    def get_server_addresses(self, server_ip):
        """Every real, currently-assigned global- and link-scope address
        across all of a server's interfaces -- not just its "main"
        IP/hostname AAAA, since a server can now have presence on more
        than one subnet (see poll_subnets()). Link-local (fe80::/10)
        addresses are included alongside global ones: they're immune to
        SLAAC/prefix-delegation renumbering (fe80::/10 is never
        delegated), which makes them a genuinely more stable thing for
        e.g. opnsense to point a DNS-server reference at than a global
        SLAAC address -- but they weren't visible anywhere before, only
        discoverable by SSHing in and running `ip addr`. Ground truth
        from the server itself rather than derived from config, so it
        naturally reflects new interfaces (VLAN presence, etc.) without
        needing code changes each time one is added. Docker's own bridge
        network and loopback are excluded -- internal plumbing, not a
        real network presence worth showing. Returns a list of
        {interface, address, version, is_vip, is_link_local} dicts, or
        []."""
        try:
            ssh = paramiko.SSHClient()
            ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            ssh.connect(server_ip, username=SSH_USER, key_filename=SSH_KEY, timeout=5)
            stdin, stdout, stderr = ssh.exec_command('ip -o addr show')
            output = stdout.read().decode()
            ssh.close()

            addresses = []
            for line in output.splitlines():
                parts = line.split()
                if len(parts) < 4:
                    continue
                iface = parts[1]
                if iface in ('lo',) or iface == 'docker0' or iface.startswith(('veth', 'br-')):
                    continue
                family = parts[2]
                if family not in ('inet', 'inet6'):
                    continue
                is_link_local = 'scope link' in line
                if not is_link_local and 'scope global' not in line:
                    continue
                if is_link_local:
                    # keepalived's VIP addresses skip DAD ('nodad') since
                    # they're already known-unique; the kernel's own
                    # auto-generated link-local (proto kernel_ll) doesn't.
                    is_vip = 'nodad' in line
                else:
                    # v4 VIPs show an explicit 'secondary' flag; v6 VIPs
                    # don't carry SLAAC's 'dynamic' flag (same signal
                    # used elsewhere in this file to tell a
                    # keepalived-assigned address apart from the
                    # interface's real one).
                    is_vip = 'secondary' in line if family == 'inet' else 'dynamic' not in line
                addresses.append({
                    'interface': iface,
                    'address': parts[3].split('/')[0],
                    'version': 6 if family == 'inet6' else 4,
                    'is_vip': is_vip,
                    'is_link_local': is_link_local,
                })
            return addresses
        except Exception as e:
            logger.error(f"Failed to read addresses for {server_ip}: {e}")
            return []

    def check_server_status(self, server_ip):
        """Check if this server's DNS service (dnsmasq or named,
        whichever it's actually running -- see the BIND9 migration's
        per-server dns_backend) is up."""
        try:
            ssh = paramiko.SSHClient()
            ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            ssh.connect(server_ip, username=SSH_USER, key_filename=SSH_KEY, timeout=5)
            # Checks for either process rather than looking up this IP's
            # dns_backend first -- simpler, and correct regardless of
            # migration state (a node should only ever have one of the
            # two actually running, given bind9-setup.yml masks named
            # until that node's real Phase 3 cutover).
            stdin, stdout, stderr = ssh.exec_command(
                "(pgrep -x dnsmasq > /dev/null || pgrep -x named > /dev/null) && echo active || echo inactive"
            )
            output = stdout.read().decode()
            ssh.close()
            return 'active' in output.lower()
        except:
            return False

    def check_keepalived_status(self, server_ip):
        """Check if server is the active keepalived master.

        Returns:
            Tuple of (is_master, keepalived_running, ipv6_vip_active). The
            v4 and v6 VIPs are kept in lockstep by a vrrp_sync_group, so
            ipv6_vip_active should always agree with is_master — checked
            independently anyway so drift (e.g. a bad keepalived.conf edit)
            shows up in monitoring rather than being silently assumed.
        """
        try:
            ssh = paramiko.SSHClient()
            ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            ssh.connect(server_ip, username=SSH_USER, key_filename=SSH_KEY, timeout=5)

            # Check if keepalived is running using pgrep (Docker-compatible)
            stdin, stdout, stderr = ssh.exec_command("pgrep -x keepalived > /dev/null && echo running || echo stopped")
            keepalived_status = stdout.read().decode().strip()
            keepalived_running = keepalived_status.lower() == 'running'

            if not keepalived_running:
                ssh.close()
                return False, False, False

            # Check if this is the master by checking VIP assignment
            vip = self.config.get('global', {}).get('keepalive_vip', '192.168.0.230')
            stdin, stdout, stderr = ssh.exec_command(f"ip addr show | grep -q {vip} && echo MASTER || echo BACKUP")
            output = stdout.read().decode().strip()

            vip6 = self.config.get('global', {}).get('keepalive_vip6')
            ipv6_vip_active = False
            if vip6:
                stdin, stdout, stderr = ssh.exec_command(f"ip -6 addr show | grep -q {vip6} && echo yes || echo no")
                ipv6_vip_active = stdout.read().decode().strip() == 'yes'

            ssh.close()

            is_master = 'MASTER' in output.upper()
            return is_master, keepalived_running, ipv6_vip_active
        except:
            return False, False, False

    def get_dynamic_hosts(self):
        """Get all dynamic-address tracked hosts."""
        return self.config.get('dynamic_hosts', [])

    def get_subnets(self):
        """Get all named subnets (CIDR + primary_dns + live prefix_v6)."""
        return self.config.get('global', {}).setdefault('subnets', {})

    def add_subnet(self, name, cidr_v4, primary_dns=None, interface='eth0'):
        """Register a new named subnet for subnet-based address tracking.
        primary_dns is a server name from `servers` (not a raw IP) --
        picking a DNS server this app already manages means one less
        thing for the user to keep in sync by hand if that server's IP
        ever changes. interface: which NIC on primary_dns is actually on
        this subnet -- defaults to eth0, but a server with more than one
        interface (e.g. a second NIC or VLAN sub-interface added
        specifically to reach a subnet it isn't otherwise on) needs the
        right one named here, or poll_subnets() would read some other
        subnet's prefix instead."""
        subnets = self.get_subnets()
        if name in subnets:
            return False, "Subnet already exists"
        if primary_dns and primary_dns not in self.get_servers():
            return False, f"'{primary_dns}' is not a known server"
        try:
            ipaddress.IPv4Network(cidr_v4, strict=False)
        except ValueError as e:
            return False, f"Invalid CIDR: {e}"
        subnets[name] = {'cidr_v4': cidr_v4, 'prefix_v6': None, 'primary_dns': primary_dns, 'interface': interface or 'eth0'}
        self.save_config()
        return True, "Subnet added"

    def update_subnet(self, name, **fields):
        """Update a subnet's cidr_v4/primary_dns/interface. prefix_v6 is
        intentionally not settable here -- it's only ever written by
        poll_subnets() detecting the live prefix from primary_dns."""
        allowed = ('cidr_v4', 'primary_dns', 'interface')
        subnets = self.get_subnets()
        if name not in subnets:
            return False, "Subnet not found"
        if 'primary_dns' in fields and fields['primary_dns'] and fields['primary_dns'] not in self.get_servers():
            return False, f"'{fields['primary_dns']}' is not a known server"
        if 'cidr_v4' in fields:
            try:
                ipaddress.IPv4Network(fields['cidr_v4'], strict=False)
            except ValueError as e:
                return False, f"Invalid CIDR: {e}"
        if 'interface' in fields and not fields['interface']:
            fields['interface'] = 'eth0'
        subnets[name].update({k: v for k, v in fields.items() if k in allowed})
        self.save_config()
        return True, "Subnet updated"

    def delete_subnet(self, name):
        """Remove a named subnet. Refuses if any dynamic_hosts entry still
        references it, rather than silently breaking that entry's
        tracking."""
        subnets = self.get_subnets()
        if name not in subnets:
            return False, "Subnet not found"
        in_use = [e['domain'] for e in self.config.get('dynamic_hosts', []) if e.get('subnet') == name]
        if in_use:
            return False, f"Still referenced by: {', '.join(in_use)}"
        del subnets[name]
        self.save_config()
        return True, "Subnet removed"

    _VLAN_NAME_RE = re.compile(r'^[a-z][a-z0-9_-]{0,30}$')

    def _write_remote_root_file(self, server_ip, content, remote_path, mode='600', owner='root', group='root'):
        """Write a file to a root-owned (by default) path on a remote
        server.

        Goes over SFTP to a temp path first, then a short, fixed-shape
        `sudo mv` — rather than embedding arbitrary content in a shell
        command line (the `echo '...' | sudo tee` pattern used elsewhere
        in this file for dnsmasq's own config, which is fine there since
        that content is DNS records, not the kind of thing that starts
        containing shell metacharacters). VLAN netplan content can include
        admin-supplied static addresses, so it goes through SFTP instead,
        where quoting doesn't apply at all.

        owner/group default to root:root but can be overridden -- BIND
        zone files are written as bind:bind (matching the directory
        ownership ansible/bind9-setup.yml already sets up), since BIND's
        inline-signing writes its own companion .signed/.jnl files into
        the same directory and expects to own what's there.
        """
        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        ssh.connect(server_ip, username=SSH_USER, key_filename=SSH_KEY, timeout=10)
        tmp_path = f"/tmp/.dnsmasq-ui-upload-{secrets.token_hex(8)}"
        try:
            sftp = ssh.open_sftp()
            with sftp.file(tmp_path, 'w') as f:
                f.write(content)
            sftp.close()
            cmd = (
                f"sudo install -o {owner} -g {group} -m {mode} {tmp_path} {remote_path} && "
                f"rm -f {tmp_path}"
            )
            stdin, stdout, stderr = ssh.exec_command(cmd)
            exit_status = stdout.channel.recv_exit_status()
            if exit_status != 0:
                raise RuntimeError(stderr.read().decode() or f"remote command exited {exit_status}")
        finally:
            ssh.close()

    def _run_remote_root_command(self, server_ip, command, timeout=30):
        """Run a fixed (not admin-content-carrying) command as root on a
        remote server. Returns (success, output_or_error)."""
        try:
            ssh = paramiko.SSHClient()
            ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            ssh.connect(server_ip, username=SSH_USER, key_filename=SSH_KEY, timeout=10)
            stdin, stdout, stderr = ssh.exec_command(f"sudo {command}", timeout=timeout)
            exit_status = stdout.channel.recv_exit_status()
            out = stdout.read().decode()
            err = stderr.read().decode()
            ssh.close()
            if exit_status != 0:
                return False, err or out or f"exited {exit_status}"
            return True, out
        except Exception as e:
            return False, str(e)

    def _run_proxmox_ssh_command(self, node, command, timeout=30):
        """Run a command over SSH on a Proxmox VE node as PROXMOX_SSH_USER
        (default root — pvesh needs it, and there's no sudo layer to go
        through the way _run_remote_root_command uses on the dnsmasq
        servers). node is a name from PROXMOX_NODE_IPS, not a raw IP."""
        host = PROXMOX_NODE_IPS.get(node)
        if not host:
            return False, f"Unknown Proxmox node '{node}' — not in PROXMOX_NODES/PROXMOX_IPS"
        try:
            ssh = paramiko.SSHClient()
            ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            ssh.connect(host, username=PROXMOX_SSH_USER, key_filename=SSH_KEY, timeout=10)
            stdin, stdout, stderr = ssh.exec_command(command, timeout=timeout)
            exit_status = stdout.channel.recv_exit_status()
            out = stdout.read().decode()
            err = stderr.read().decode()
            ssh.close()
            if exit_status != 0:
                return False, err or out or f"exited {exit_status}"
            return True, out
        except Exception as e:
            return False, str(e)

    def get_proxmox_interface(self, node, iface):
        """Fetch a Proxmox VE node's network interface config via pvesh
        (over SSH, see _run_proxmox_ssh_command). Raises RuntimeError on
        failure rather than returning a (success, ...) tuple, since every
        caller needs the parsed dict or nothing useful to do."""
        ok, output = self._run_proxmox_ssh_command(
            node, f"pvesh get /nodes/{node}/network/{iface} --output-format json")
        if not ok:
            raise RuntimeError(f"pvesh get failed for {node}/{iface}: {output}")
        return json.loads(output)

    # Fields safe to round-trip through `pvesh set` for a type=vlan
    # interface, i.e. everything a plain tagged VLAN sub-interface can
    # actually have. Deliberately excludes bond/bridge/OVS-specific
    # fields (bridge_ports, ovs_*, slaves, etc.) this hasn't been
    # validated against — update_proxmox_interface_v6 refuses any
    # interface whose type isn't 'vlan' rather than guessing.
    # cidr/cidr6 (combined address+mask) rather than separate
    # address+netmask pairs — GET returns netmask as a bare prefix
    # length ("24"), but `pvesh set --netmask` wants dotted-quad
    # notation instead, confirmed the hard way. cidr/cidr6 avoid the
    # mismatch entirely and Proxmox derives address/netmask (and
    # address6/netmask6) from them automatically.
    _PVESH_VLAN_FIELDS = ('cidr', 'gateway', 'cidr6', 'gateway6', 'mtu', 'autostart',
                           'comments', 'comments6', 'vlan-id', 'vlan-raw-device')

    def _build_pvesh_set_cmd(self, node, iface, fields):
        """Build a `pvesh set` command string for a type=vlan interface
        from a fields dict (see _PVESH_VLAN_FIELDS) — shared by the real
        update and by the revert-timer command scheduled against the
        node's own systemd (see _schedule_proxmox_revert), so both stay
        in sync with the same field-quoting logic."""
        cmd = ["pvesh", "set", f"/nodes/{node}/network/{iface}", "--type", "vlan"]
        for k, v in fields.items():
            if isinstance(v, bool):
                v = 1 if v else 0
            cmd += [f"--{k}", shlex.quote(str(v))]
        return " ".join(cmd)

    def _schedule_proxmox_revert(self, node, iface, revert_fields, timeout_seconds):
        """Schedule an unconditional self-revert on the Proxmox node
        itself, via a transient systemd timer (systemd-run --on-active —
        no extra package needed, unlike `at`, which isn't installed on
        these nodes). This must be scheduled BEFORE the real change is
        ever applied: if it fires, it restores revert_fields (the
        interface's config from just before the change) and applies —
        running locally on the node's own systemd, so it fires even if
        dnsmasq-ui itself loses all contact with the node right after
        applying the real change. Returns (success, unit_name_or_error)."""
        revert_cmd = self._build_pvesh_set_cmd(node, iface, revert_fields)
        apply_cmd = f"pvesh set /nodes/{node}/network"
        unit = f"dnsmasq-ui-revert-{iface}"
        inner = f"{revert_cmd} && {apply_cmd}"
        schedule_cmd = (
            f"systemd-run --on-active={int(timeout_seconds)}s --unit={shlex.quote(unit)} "
            f"--description={shlex.quote(f'dnsmasq-ui auto-revert safety net for {iface}')} "
            f"/bin/bash -c {shlex.quote(inner)}"
        )
        ok, output = self._run_proxmox_ssh_command(node, schedule_cmd)
        if not ok:
            return False, f"Failed to schedule revert timer for {node}/{iface}: {output}"
        return True, unit

    def _cancel_proxmox_revert(self, node, iface):
        """Cancel a revert timer scheduled by _schedule_proxmox_revert —
        called once a pushed change is applied and independently
        confirmed reachable. Deliberately NOT called on a failed/unclear
        outcome — an uncancelled timer is exactly the safety net that's
        supposed to fire in that case."""
        unit = f"dnsmasq-ui-revert-{iface}"
        return self._run_proxmox_ssh_command(node, f"systemctl stop {shlex.quote(unit)}.timer")

    def _validate_proxmox_reachability(self, node, address6, timeout=10):
        """Confirm a Proxmox node is actually reachable/usable at a
        newly-applied IPv6 address — run from wherever dnsmasq-ui itself
        is, NOT via SSH into the node, since the point is proving the
        network path TO it still works, which an SSH session already
        established from inside a prior step can't demonstrate.

        Originally used ping6 here, which turned out to be the wrong
        signal on this network: confirmed live against both pve04 and
        pve06 that ICMPv6 echo gets dropped by conntrack (ctstate
        INVALID) for freshly-applied addresses specifically, even
        though the address is fully working for real traffic the
        whole time — proven directly when the user successfully loaded
        the Proxmox web UI at the exact address ping6 called
        unreachable, and confirmed independently with a plain TCP
        connect to the same address/port succeeding on the first try.
        So this checks the thing that actually matters instead: a TCP
        connect to the Proxmox API/UI port (8006) on the new address
        itself — a real functional test of "is this address usable",
        immune to whatever is eating ICMP echo here. A fresh SSH
        connect to the node's stable mgmt IP is still checked too
        (proves the box itself is fully up, not e.g. mid-reboot from a
        botched network reload) — just no longer via ping.
        """
        try:
            with socket.create_connection((address6, 8006), timeout=timeout):
                pass
        except Exception as e:
            return False, f"TCP connect to [{address6}]:8006 (Proxmox UI/API) failed: {e}"

        host = PROXMOX_NODE_IPS.get(node)
        try:
            ssh = paramiko.SSHClient()
            ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            ssh.connect(host, username=PROXMOX_SSH_USER, key_filename=SSH_KEY, timeout=timeout)
            ssh.close()
        except Exception as e:
            return False, f"SSH connect to {node} ({host}) failed after applying: {e}"

        return True, f"[{address6}]:8006 reachable, SSH to {node} OK"

    def update_proxmox_interface_v6(self, node, iface, address6, apply=True):
        """Push a new static IPv6 address onto a Proxmox VE node's own
        VLAN interface (e.g. pve01's vlan7 mgmt-subnet presence) — the
        device-side counterpart to a dynamic_hosts entry's ipv6_host,
        for when poll_subnets() detects the underlying subnet's prefix
        has actually drifted (see _propagate_subnet_prefix_change).

        Proxmox's network API is a full replace, not a merge — confirmed
        directly against pve01: sending only address6/netmask6/gateway6
        silently dropped the interface's entire IPv4 side from the
        pending config (families went from ["inet","inet6"] down to
        ["inet6"]). So this always re-fetches the interface's current
        full config and resends every field unchanged except cidr6 (which
        Proxmox derives address6/netmask6 from), rather than a scoped
        update. Scoped to type=vlan interfaces only (see
        _PVESH_VLAN_FIELDS).

        After staging the change, re-fetches and diffs against what was
        expected — if anything besides cidr6/address6 differs, reverts
        (`pvesh delete .../network`, which discards all pending changes)
        and returns failure instead of applying. apply=False stops after
        that verification, leaving the change staged-but-not-applied
        (visible in the Proxmox UI's pending-changes banner) rather than
        running `ifreload -a` — useful to test without ever touching
        live networking.

        Returns (success, message).
        """
        try:
            current = self.get_proxmox_interface(node, iface)
        except Exception as e:
            return False, f"Failed to read current config for {node}/{iface}: {e}"

        if current.get('type') != 'vlan':
            return False, (f"{node}/{iface} is type={current.get('type')!r}, not 'vlan' — "
                            "refusing (field mapping only validated for vlan interfaces)")

        try:
            new_addr = ipaddress.IPv6Address(address6)
        except ValueError:
            return False, f"'{address6}' is not a valid IPv6 address"

        if 'netmask6' not in current:
            return False, f"{node}/{iface} has no existing netmask6 — refusing to guess one"
        new_cidr6 = f"{new_addr}/{current['netmask6']}"

        fields = {k: current[k] for k in self._PVESH_VLAN_FIELDS
                  if current.get(k) is not None}
        fields['cidr6'] = new_cidr6

        ok, output = self._run_proxmox_ssh_command(node, self._build_pvesh_set_cmd(node, iface, fields))
        if not ok:
            return False, f"pvesh set failed for {node}/{iface}: {output}"

        try:
            pending = self.get_proxmox_interface(node, iface)
        except Exception as e:
            self._run_proxmox_ssh_command(node, f"pvesh delete /nodes/{node}/network")
            return False, f"Failed to verify pending change for {node}/{iface}, reverted: {e}"

        expected = dict(current)
        expected['cidr6'] = new_cidr6
        expected['address6'] = str(new_addr)
        ignore = ('active', 'exists', 'priority', 'families', 'method', 'method6')
        unexpected_diff = {k: (expected.get(k), pending.get(k))
                            for k in set(expected) | set(pending)
                            if k not in ignore and expected.get(k) != pending.get(k)}
        if unexpected_diff:
            self._run_proxmox_ssh_command(node, f"pvesh delete /nodes/{node}/network")
            return False, f"Pending change for {node}/{iface} didn't match expectations, reverted: {unexpected_diff}"

        if not apply:
            return True, f"{node}/{iface} staged to {new_addr} (pending, not applied)"

        ok, output = self._run_proxmox_ssh_command(node, f"pvesh set /nodes/{node}/network")
        if not ok:
            return False, f"pvesh apply failed for {node}/{iface} (still pending, not reverted): {output}"
        return True, f"{node}/{iface} updated to {new_addr} and applied"

    def provision_proxmox_interface_v6(self, node, iface, address6, gateway6, apply=True):
        """One-time setup of static IPv6 on a Proxmox VE VLAN interface
        that currently has NONE at all (no netmask6 in its config) —
        e.g. a node whose vlan7 only ever got IPv4 configured. Separate
        from update_proxmox_interface_v6 (which requires an existing
        IPv6 config to safely diff against) because there's nothing to
        diff against or revert to here: on failure, "revert" means
        removing the IPv6 config entirely (back to IPv4-only), not
        restoring some prior IPv6 that never existed. Refuses outright
        if the interface already has netmask6 — that's
        update_proxmox_interface_v6's job, not this one's.

        address6 is always applied at /64, matching every other
        mgmt-VLAN presence in this project. gateway6 must be supplied
        explicitly — there's no prior value to preserve here;
        commit_proxmox_interface_v6 derives it as the /64 network's own
        address, matching the pattern already established by pve01's
        real, manually-configured interface.

        Returns (success, message). apply=False stops after staging +
        verifying, same meaning as update_proxmox_interface_v6.
        """
        try:
            current = self.get_proxmox_interface(node, iface)
        except Exception as e:
            return False, f"Failed to read current config for {node}/{iface}: {e}"

        if current.get('type') != 'vlan':
            return False, (f"{node}/{iface} is type={current.get('type')!r}, not 'vlan' — "
                            "refusing (field mapping only validated for vlan interfaces)")

        if 'netmask6' in current:
            return False, (f"{node}/{iface} already has IPv6 configured (netmask6={current['netmask6']}) "
                            "— use the normal update path, not provisioning")

        try:
            new_addr = ipaddress.IPv6Address(address6)
            new_gw = ipaddress.IPv6Address(gateway6)
        except ValueError as e:
            return False, f"Invalid IPv6 address: {e}"

        new_cidr6 = f"{new_addr}/64"
        fields = {k: current[k] for k in self._PVESH_VLAN_FIELDS if current.get(k) is not None}
        fields['cidr6'] = new_cidr6
        fields['gateway6'] = str(new_gw)

        ok, output = self._run_proxmox_ssh_command(node, self._build_pvesh_set_cmd(node, iface, fields))
        if not ok:
            return False, f"pvesh set failed for {node}/{iface}: {output}"

        try:
            pending = self.get_proxmox_interface(node, iface)
        except Exception as e:
            self._run_proxmox_ssh_command(node, f"pvesh delete /nodes/{node}/network")
            return False, f"Failed to verify pending change for {node}/{iface}, reverted: {e}"

        # Baseline from `current` (every field Proxmox actually returns,
        # e.g. address/netmask/type), not `fields` (only the write
        # whitelist) -- confirmed the hard way: starting from `fields`
        # meant `expected` was missing address/netmask/type entirely,
        # so the diff check saw pending's real values as "unexpected"
        # and reverted a perfectly correct provision.
        expected = dict(current)
        expected['cidr6'] = new_cidr6
        expected['gateway6'] = str(new_gw)
        expected['address6'] = str(new_addr)
        # netmask6 is ignored here specifically because provisioning
        # starts with none at all in `current`/`fields` — Proxmox
        # derives it from cidr6 once applied, so pending will have it
        # even though expected never did; that's correct, not a
        # mismatch (unlike update_proxmox_interface_v6, where an
        # existing netmask6 is preserved and directly comparable).
        ignore = ('active', 'exists', 'priority', 'families', 'method', 'method6', 'netmask6')
        unexpected_diff = {k: (expected.get(k), pending.get(k))
                            for k in set(expected) | set(pending)
                            if k not in ignore and expected.get(k) != pending.get(k)}
        if unexpected_diff:
            self._run_proxmox_ssh_command(node, f"pvesh delete /nodes/{node}/network")
            return False, f"Pending change for {node}/{iface} didn't match expectations, reverted: {unexpected_diff}"

        if not apply:
            return True, f"{node}/{iface} staged to provision {new_addr} (pending, not applied)"

        ok, output = self._run_proxmox_ssh_command(node, f"pvesh set /nodes/{node}/network")
        if not ok:
            return False, f"pvesh apply failed for {node}/{iface} (still pending, not reverted): {output}"
        return True, f"{node}/{iface} provisioned with {new_addr} and applied"

    def commit_proxmox_interface_v6(self, node, iface, address6):
        """Full commit-confirm push of a new static IPv6 address to a
        Proxmox VE node's own interface — the safe wrapper that
        _propagate_subnet_prefix_change (and the manual "Sync Now"
        path) actually calls.

        Dispatches to one of two underlying implementations depending
        on whether the interface already has IPv6 configured at all
        (netmask6 present in its current config): update_proxmox_
        interface_v6 for an existing address (diffs against and can
        revert to what was there before), or provision_proxmox_
        interface_v6 for a bare IPv4-only interface (nothing to diff
        against — confirmed live: pve04/pve06's vlan7 had IPv4 only,
        while pve01/pve3 already had some IPv6). gateway6 for
        provisioning is derived here as the /64 network's own address
        (e.g. address6 = ...::14 -> gateway6 = ...::), matching the
        pattern pve01's real, manually-configured interface already
        established — not something either underlying method guesses
        on its own.

        Order matters: the self-revert timer (_schedule_proxmox_revert)
        is scheduled FIRST, on the node's own systemd, before the real
        change is ever applied — so it fires unconditionally within
        PROXMOX_COMMIT_TIMEOUT_SECONDS unless explicitly cancelled,
        including in the case a plain client-side revert can't cover:
        the push breaks dnsmasq-ui's own connectivity back to the node.
        Only cancelled once the change is both applied AND independently
        confirmed reachable (_validate_proxmox_reachability) — a failure
        at any step leaves the timer running (or nothing was ever
        applied) rather than trying to clean up from this side.

        Returns (success, message).
        """
        try:
            current = self.get_proxmox_interface(node, iface)
        except Exception as e:
            return False, f"Failed to read current config for {node}/{iface}: {e}"

        provisioning = 'netmask6' not in current
        revert_fields = {k: current[k] for k in self._PVESH_VLAN_FIELDS if current.get(k) is not None}
        scheduled, detail = self._schedule_proxmox_revert(node, iface, revert_fields, PROXMOX_COMMIT_TIMEOUT_SECONDS)
        if not scheduled:
            return False, (f"Refusing to proceed — couldn't schedule the safety-net revert timer "
                            f"for {node}/{iface}: {detail}")

        if provisioning:
            gateway6 = str(ipaddress.ip_interface(f"{address6}/64").network.network_address)
            success, message = self.provision_proxmox_interface_v6(node, iface, address6, gateway6, apply=True)
        else:
            success, message = self.update_proxmox_interface_v6(node, iface, address6, apply=True)
        if not success:
            self._cancel_proxmox_revert(node, iface)
            verb = "Provisioning" if provisioning else "Update"
            return False, f"{verb} failed, nothing was left applied (revert timer cancelled, nothing to revert): {message}"

        valid, valid_detail = self._validate_proxmox_reachability(node, address6)
        if not valid:
            return False, (f"Applied but failed post-apply validation ({valid_detail}) — NOT cancelling "
                            f"the revert timer; {node}/{iface} will self-revert to its previous config "
                            f"within {PROXMOX_COMMIT_TIMEOUT_SECONDS}s if this isn't resolved first")

        cancel_ok, cancel_detail = self._cancel_proxmox_revert(node, iface)
        if not cancel_ok:
            return False, (f"Applied and validated OK, but failed to cancel the revert timer "
                            f"({cancel_detail}) — {node}/{iface} WILL self-revert in "
                            f"{PROXMOX_COMMIT_TIMEOUT_SECONDS}s unless stopped manually: "
                            f"systemctl stop dnsmasq-ui-revert-{iface}.timer")

        return True, f"{node}/{iface} committed to {address6} (validated reachable, revert timer cancelled)"

    # --- Group Update Plans -------------------------------------------
    #
    # A "group" is a declared set of HA members that must ALL converge to
    # a target state before the group counts as updated — a level above
    # commit_proxmox_interface_v6, which only guarantees one node's
    # commit-confirm handshake is safe. Different group *types* need
    # completely different commit/verify mechanics (Proxmox's pvesh/
    # systemd-run dance is nothing like what a future opnsense/pfsense
    # HA pair will need), so each group names a "script" — a pair of
    # methods on this class, registered below — rather than the
    # framework hardcoding how any particular kind of member gets
    # updated. Proxmox VLAN presence (pve01 etc.) is the first script;
    # see the [[project-opnsense-dhcpv6-drift]] work for the next one.
    #
    # zones.json shape (global.update_groups):
    #   { "<group_name>": {
    #       "description": str, "script": "<name from _UPDATE_GROUP_SCRIPTS>",
    #       "members": ["<dynamic_hosts domain>", ...],
    #       "lock": {"locked": bool, "reason": str|None, "member": str|None, "since": str|None}
    #   } }

    def _script_proxmox_vlan_commit(self, entry, target_address):
        """proxmox_vlan_commit's commit function — thin adapter from the
        group framework's generic (entry, target_state) shape onto
        commit_proxmox_interface_v6's (node, iface, address6)."""
        proxmox = entry.get('proxmox_update')
        if not proxmox:
            return False, f"{entry['domain']} has no proxmox_update configured"
        return self.commit_proxmox_interface_v6(proxmox['node'], proxmox['iface'], target_address)

    def _script_proxmox_vlan_verify(self, entry):
        """proxmox_vlan_commit's verify function — does this member's
        live Proxmox interface match what it's currently expected to be
        (recomputed from the subnet's live prefix, not cached), and is
        it independently reachable? Used by verify_and_clear_group_lock,
        not by the commit path itself."""
        proxmox = entry.get('proxmox_update')
        if not proxmox or entry.get('ipv6_host') is None:
            return False, "not configured for proxmox_vlan_commit (missing ipv6_host/proxmox_update)"
        subnet = self.get_subnets().get(entry.get('subnet'), {})
        prefix_v6 = subnet.get('prefix_v6')
        if not prefix_v6:
            return False, "subnet has no live prefix_v6 to check against"
        try:
            expected = str(_ipv6_from_prefix_and_host(ipaddress.ip_network(prefix_v6), entry['ipv6_host']))
        except ValueError as e:
            return False, f"address computation failed: {e}"
        try:
            live = self.get_proxmox_interface(proxmox['node'], proxmox['iface'])
        except Exception as e:
            return False, f"couldn't read {proxmox['node']}/{proxmox['iface']}: {e}"
        if live.get('address6') != expected:
            return False, f"{proxmox['node']}/{proxmox['iface']} has {live.get('address6')}, expected {expected}"
        valid, detail = self._validate_proxmox_reachability(proxmox['node'], expected)
        return valid, (detail if valid else f"address matches but unreachable: {detail}")

    # Registry of group scripts: group_name -> method names on this
    # class implementing commit(entry, target_state) -> (bool, str) and
    # verify(entry) -> (bool, str). Deliberately code, not zones.json
    # data — a script is real logic (SSH/API calls, vendor-specific
    # quirks), not something safe to define from the Config page.
    _UPDATE_GROUP_SCRIPTS = {
        'proxmox_vlan_commit': {
            'commit': '_script_proxmox_vlan_commit',
            'verify': '_script_proxmox_vlan_verify',
        },
    }

    def get_update_groups(self):
        """All declared update groups (see the Group Update Plans note
        above). Lazily created as empty so older zones.json files don't
        need a migration."""
        return self.config.get('global', {}).setdefault('update_groups', {})

    def get_dynamic_host(self, domain):
        return next((e for e in self.config.get('dynamic_hosts', []) if e['domain'] == domain), None)

    def add_group_member(self, group_name, domain):
        """Add a dynamic_hosts entry (by domain) as a member of a
        declared update group. Doesn't require the entry to already be
        configured for the group's script (e.g. proxmox_update set) —
        that's checked at commit/verify time, not membership time, so a
        member can be added ahead of finishing its own config."""
        groups = self.get_update_groups()
        group = groups.get(group_name)
        if not group:
            return False, f"Unknown update group '{group_name}'"
        if not self.get_dynamic_host(domain):
            return False, f"No dynamic_hosts entry for '{domain}' — track it first"
        members = group.setdefault('members', [])
        if domain in members:
            return False, f"'{domain}' is already a member of '{group_name}'"
        members.append(domain)
        self.save_config()
        return True, f"'{domain}' added to '{group_name}'"

    def remove_group_member(self, group_name, domain):
        """Remove a member from a declared update group. Refuses while
        the group is locked — removing the failing member out from under
        a lock would let verify_and_clear_group_lock report 'all good'
        without the actual problem ever having been resolved."""
        groups = self.get_update_groups()
        group = groups.get(group_name)
        if not group:
            return False, f"Unknown update group '{group_name}'"
        lock = group.get('lock', {})
        if lock.get('locked') and lock.get('member') == domain:
            return False, (f"'{group_name}' is locked on '{domain}' — unlock (Verify & Unlock) "
                            "before removing it, not instead of resolving it")
        members = group.setdefault('members', [])
        if domain not in members:
            return False, f"'{domain}' is not a member of '{group_name}'"
        members.remove(domain)
        self.save_config()
        return True, f"'{domain}' removed from '{group_name}'"

    def run_update_group(self, group_name, member_targets):
        """Run a Group Update Plan: process every member with a target
        this cycle strictly one at a time, through its script's commit
        function, and only consider the group updated once every one of
        them has converged. Any failure halts the rest of the group
        immediately — remaining members are left completely untouched —
        and locks that GROUP specifically (other groups are unaffected)
        until an admin unlocks it via verify_and_clear_group_lock.

        member_targets: {domain: target_state} for every member that
        actually needs to converge this cycle — a member not in this
        dict is left alone (e.g. nothing changed for it). target_state
        is opaque to this method, passed straight through to the
        script's commit function (an IPv6 address string for
        proxmox_vlan_commit; a future script might want something
        else entirely).

        Returns (all_converged, results) — results is a per-member list
        of {'domain', 'ok', 'detail'} regardless of outcome.
        """
        groups = self.get_update_groups()
        group = groups.get(group_name)
        if not group:
            return False, [{'domain': None, 'ok': False, 'detail': f"Unknown update group '{group_name}'"}]

        lock = group.setdefault('lock', {'locked': False, 'reason': None, 'member': None, 'since': None})
        if lock.get('locked'):
            logger.error(f"Update group '{group_name}' is LOCKED ({lock.get('reason')}) since "
                         f"{lock.get('since')} — skipping all updates for this group until unlocked")
            return False, [{'domain': None, 'ok': False, 'detail': f"Group locked: {lock.get('reason')}"}]

        script = self._UPDATE_GROUP_SCRIPTS.get(group.get('script'))
        if not script:
            return False, [{'domain': None, 'ok': False,
                             'detail': f"Unknown script '{group.get('script')}' for group '{group_name}'"}]
        commit_fn = getattr(self, script['commit'])

        members = [d for d in group.get('members', []) if d in member_targets]
        results = []
        for i, domain in enumerate(members):
            entry = self.get_dynamic_host(domain)
            if not entry:
                results.append({'domain': domain, 'ok': False, 'detail': 'dynamic_hosts entry not found'})
                continue
            target = member_targets[domain]
            success, message = commit_fn(entry, target)
            results.append({'domain': domain, 'ok': success, 'detail': message})
            logger.warning(f"Group '{group_name}' member {domain}: {'OK' if success else 'FAILED'} — {message}")

            if success:
                self._notify_group_member_success(group_name, domain, target, message)
                continue

            skipped = members[i + 1:]
            lock.update({'locked': True, 'reason': message, 'member': domain,
                         'since': datetime.now().isoformat()})
            self.save_config()
            self._notify_group_halted(group_name, domain, message, skipped)
            return False, results

        logger.warning(f"Update group '{group_name}': all {len(members)} member(s) converged")
        return True, results

    def verify_and_clear_group_lock(self, group_name):
        """Re-verify every member of a locked group via its script's
        verify function before clearing the lock. An admin clicking
        'unlock' isn't enough on its own — this re-checks live state
        itself rather than trusting the click. Returns (success,
        message, details) — details is a per-member list regardless of
        outcome, so a partial failure clearly shows which one(s) are
        still bad."""
        groups = self.get_update_groups()
        group = groups.get(group_name)
        if not group:
            return False, f"Unknown update group '{group_name}'", []
        lock = group.setdefault('lock', {'locked': False, 'reason': None, 'member': None, 'since': None})
        if not lock.get('locked'):
            return True, "Not locked", []
        script = self._UPDATE_GROUP_SCRIPTS.get(group.get('script'))
        if not script:
            return False, f"Unknown script '{group.get('script')}' for group '{group_name}'", []
        verify_fn = getattr(self, script['verify'])

        details = []
        all_good = True
        for domain in group.get('members', []):
            entry = self.get_dynamic_host(domain)
            if not entry:
                details.append({'domain': domain, 'ok': False, 'detail': 'dynamic_hosts entry not found'})
                all_good = False
                continue
            ok, detail = verify_fn(entry)
            details.append({'domain': domain, 'ok': ok, 'detail': detail})
            if not ok:
                all_good = False

        if not all_good:
            return False, "One or more members still don't match expectations — see details", details

        lock.update({'locked': False, 'reason': None, 'member': None, 'since': None})
        self.save_config()
        return True, "All group members verified good, lock cleared", details

    def _notify_group_member_success(self, group_name, domain, target, message):
        auth_config = _load_auth() or {}
        to_addr = auth_config.get('two_factor', {}).get('email', {}).get('to')
        if not to_addr:
            logger.error(f"Group '{group_name}' member {domain} converged to {target}: {message} "
                         "— no notification email configured (enable email 2FA to set one)")
            return
        _send_email(
            to_addr, f"dnsmasq-ui: Update group '{group_name}' member converged ({domain})",
            f"Member {domain} of update group '{group_name}' converged to its new target.\n\n"
            f"Target: {target}\nDetail: {message}\n\nDashboard: {DASHBOARD_URL}/config"
        )

    def _notify_group_halted(self, group_name, domain, message, skipped_domains):
        """Email when a failed member commit halts a Group Update Plan
        and locks that group (see run_update_group)."""
        auth_config = _load_auth() or {}
        to_addr = auth_config.get('two_factor', {}).get('email', {}).get('to')
        skipped_note = (
            f"\n\nThe following {len(skipped_domains)} other member(s) of '{group_name}' were NOT "
            f"touched this cycle as a result (left exactly as they were, not attempted):\n" +
            "\n".join(f"  - {d}" for d in skipped_domains)
        ) if skipped_domains else "\n\nNo other members were pending an update this cycle."
        body = (
            f"Update group '{group_name}' FAILED at member {domain}.\n\nDetail: {message}\n"
            f"{skipped_note}\n\n"
            f"Group '{group_name}' is now LOCKED — no further updates will be attempted for it "
            "(other groups are unaffected) until this is reviewed and unlocked from the "
            "Configuration page, which re-verifies every member's live state before clearing the "
            f"lock.\n\nDashboard: {DASHBOARD_URL}/config"
        )
        if not to_addr:
            logger.error(f"Update group '{group_name}' halted at {domain}: {message} "
                         "— no notification email configured (enable email 2FA to set one)")
            return
        _send_email(to_addr, f"dnsmasq-ui: Update group '{group_name}' FAILED, LOCKED (member: {domain})", body)

    # Comment marker on ipset entries this code manages, so they're
    # identifiable at a glance in the Proxmox UI/CLI and don't get
    # mistaken for one of the many unrelated hand-maintained entries
    # (Ceph storage/mgmt networks, NAS, backup server, etc.) sharing the
    # same cluster-wide ipset — see update_proxmox_ipset_cidr.
    _PROXMOX_IPSET_COMMENT_PREFIX = "dnsmasq-ui-managed: "

    def get_proxmox_ipset_tracking(self):
        """Subnets whose live prefix_v6 should be mirrored into a
        Proxmox cluster-wide firewall IPSet CIDR entry (see
        update_proxmox_ipset_cidr) — keyed by subnet name. Lazily
        created as empty so older zones.json files don't need a
        migration."""
        return self.config.get('global', {}).setdefault('proxmox_ipset_tracking', {})

    def set_proxmox_ipset_tracking(self, subnet_name, ipset, label=None):
        """Start (or reconfigure) tracking a subnet's live prefix into
        a Proxmox cluster IPSet CIDR entry. Doesn't push anything to
        Proxmox immediately — 'cidr' is seeded from the subnet's
        current known prefix_v6 (if any), so if the ipset already has
        the right entry (the common case: a human set it up manually,
        same as how pve04's original fix happened), this doesn't try to
        create a duplicate — it just starts tracking from here, and
        only actually adds/removes anything the next time
        poll_subnets() detects that subnet's prefix has actually
        changed."""
        if subnet_name not in self.get_subnets():
            return False, f"Unknown subnet '{subnet_name}'"
        tracking = self.get_proxmox_ipset_tracking()
        tracking[subnet_name] = {
            'ipset': ipset,
            'cidr': self.get_subnets()[subnet_name].get('prefix_v6'),
            'label': label or subnet_name,
        }
        self.save_config()
        return True, f"Now tracking subnet '{subnet_name}' into ipset '{ipset}'"

    def _verify_subnet_ping_reachability(self, subnet_name, timeout=5):
        """Ping every subnet-tracked, ipv6_host-based dynamic_hosts
        entry on the given subnet — used specifically to confirm a
        Proxmox ipset/firewall change actually fixed the ICMPv6
        ctstate-INVALID issue it was meant to address (see
        update_proxmox_ipset_cidr), NOT as a general reachability gate.
        ping6 proved unreliable for that purpose — see
        _validate_proxmox_reachability's TCP-based check, which
        replaced it there — but it's exactly the right tool here, since
        ping is the actual symptom the ipset fix targets.

        Returns (all_ok, details) — details is a list of
        {'domain', 'ok', 'detail'} regardless of outcome, empty list
        (all_ok=True) if there's nothing on this subnet to check.
        """
        prefix_v6 = self.get_subnets().get(subnet_name, {}).get('prefix_v6')
        if not prefix_v6:
            return False, [{'domain': None, 'ok': False,
                             'detail': f"subnet '{subnet_name}' has no live prefix_v6 to check against"}]

        details = []
        for entry in self.config.get('dynamic_hosts', []):
            if entry.get('subnet') != subnet_name or entry.get('ipv6_host') is None:
                continue
            try:
                addr = str(_ipv6_from_prefix_and_host(ipaddress.ip_network(prefix_v6), entry['ipv6_host']))
            except ValueError as e:
                details.append({'domain': entry['domain'], 'ok': False, 'detail': f"address computation failed: {e}"})
                continue
            try:
                ping = subprocess.run([PING_BIN, '-6', '-c', '3', '-W', str(timeout), addr],
                                       capture_output=True, timeout=timeout + 5)
                ok = ping.returncode == 0
            except Exception as e:
                ok = False
            details.append({'domain': entry['domain'], 'ok': ok,
                             'detail': f"ping6 to {addr} {'OK' if ok else 'got no response'}"})

        if not details:
            return True, details
        return all(d['ok'] for d in details), details

    def sync_proxmox_ipset_tracking(self, subnet_name):
        """Manually (re)sync a tracked subnet's ipset CIDR entry to its
        current live prefix_v6 right now, instead of waiting for
        poll_subnets() to detect an actual prefix change. Needed for
        two cases update_proxmox_ipset_cidr's drift-triggered path
        can't handle: first-time setup (create the entry without
        waiting for a "change"), and recovering after a human manually
        removed the entry — in both cases the tracked prefix hasn't
        technically "changed", so this always checks the ipset's real
        current contents rather than trusting the locally-recorded
        'cidr', and (re)creates the entry if it's actually missing.

        Returns (success, message).
        """
        tracking = self.get_proxmox_ipset_tracking().get(subnet_name)
        if not tracking:
            return False, f"Subnet '{subnet_name}' is not configured for ipset tracking"
        current_prefix = self.get_subnets().get(subnet_name, {}).get('prefix_v6')
        if not current_prefix:
            return False, f"Subnet '{subnet_name}' has no live prefix_v6 to sync"

        node = next(iter(PROXMOX_NODE_IPS), None)
        if not node:
            return False, "No Proxmox nodes configured (PROXMOX_NODES/PROXMOX_IPS)"
        ipset = tracking['ipset']
        ok, output = self._run_proxmox_ssh_command(
            node, f"pvesh get /cluster/firewall/ipset/{shlex.quote(ipset)} --output-format json")
        if not ok:
            return False, f"Failed to read ipset '{ipset}': {output}"
        try:
            entries = json.loads(output)
        except Exception as e:
            return False, f"Failed to parse ipset '{ipset}' listing: {e}"

        already_present = any(e.get('cidr') == current_prefix for e in entries)
        if not already_present:
            old_cidr = tracking.get('cidr')
            comment = f"{self._PROXMOX_IPSET_COMMENT_PREFIX}{tracking.get('label', subnet_name)}"
            ok, output = self._run_proxmox_ssh_command(
                node, f"pvesh create /cluster/firewall/ipset/{shlex.quote(ipset)} "
                      f"--cidr {shlex.quote(current_prefix)} --comment {shlex.quote(comment)}")
            if not ok:
                return False, f"Failed to add ipset entry {current_prefix}: {output}"

            if old_cidr and old_cidr != current_prefix and any(e.get('cidr') == old_cidr for e in entries):
                old_cidr_encoded = urllib.parse.quote(old_cidr, safe='')
                ok, output = self._run_proxmox_ssh_command(
                    node, f"pvesh delete /cluster/firewall/ipset/{shlex.quote(ipset)}/{old_cidr_encoded}")
                if not ok:
                    tracking['cidr'] = current_prefix
                    self.save_config()
                    return False, (f"Added {current_prefix} but failed to remove stale old entry {old_cidr} "
                                    f"(both now present, safe but should be cleaned up manually): {output}")

        tracking['cidr'] = current_prefix
        self.save_config()

        base_msg = (f"ipset '{ipset}' already has {current_prefix} — nothing to do" if already_present
                    else f"ipset '{ipset}' now has {current_prefix}")
        ping_ok, ping_details = self._verify_subnet_ping_reachability(subnet_name)
        if not ping_details:
            return True, base_msg
        if ping_ok:
            return True, f"{base_msg} (ping6 verified OK for all {len(ping_details)} tracked host(s) on this subnet)"
        failed = [d['domain'] for d in ping_details if not d['ok']]
        return False, (f"{base_msg}, but ping6 verification is still failing for: {', '.join(failed)} — "
                        f"the ipset entry itself is correct and was kept (nothing better to fall back to), "
                        f"but the underlying connectivity issue this entry is meant to fix may not actually "
                        f"be resolved yet")

    def update_proxmox_ipset_cidr(self, ipset, old_cidr, new_cidr, label):
        """Move a CIDR entry in a cluster-wide Proxmox firewall IPSet
        from old_cidr to new_cidr — keeps a subnet-tracking ipset entry
        (e.g. the ICMPv6 ctstate-INVALID workaround discovered for the
        mgmt subnet, see project memory) correct across an ISP prefix
        renumber, since an ipset entry doesn't update on its own the
        way VLAN interface addresses do via the Group Update Plan.

        Cluster-level API (not per-node) — one call, applies everywhere
        in the cluster via corosync sync, same as the original manual
        fix. Adds the new CIDR BEFORE removing the old one, so there's
        never a window where neither is present. Only ever touches
        CIDR strings that exactly match old_cidr/new_cidr, tagged with
        _PROXMOX_IPSET_COMMENT_PREFIX — this ipset also holds unrelated,
        hand-maintained entries that must never be touched.

        Returns (success, message).
        """
        if old_cidr == new_cidr:
            return True, "No change (old and new CIDR are identical)"

        node = next(iter(PROXMOX_NODE_IPS), None)
        if not node:
            return False, "No Proxmox nodes configured (PROXMOX_NODES/PROXMOX_IPS)"

        comment = f"{self._PROXMOX_IPSET_COMMENT_PREFIX}{label}"
        create_cmd = (f"pvesh create /cluster/firewall/ipset/{shlex.quote(ipset)} "
                       f"--cidr {shlex.quote(new_cidr)} --comment {shlex.quote(comment)}")
        ok, output = self._run_proxmox_ssh_command(node, create_cmd)
        if not ok:
            return False, f"Failed to add new ipset entry {new_cidr}: {output}"

        try:
            ok, output = self._run_proxmox_ssh_command(
                node, f"pvesh get /cluster/firewall/ipset/{shlex.quote(ipset)} --output-format json")
            entries = json.loads(output) if ok else []
        except Exception as e:
            return False, f"Added {new_cidr} but couldn't verify it landed (old entry {old_cidr} left in place, safe): {e}"
        if not any(e.get('cidr') == new_cidr for e in entries):
            return False, f"Added {new_cidr} but it isn't showing up in the ipset (old entry {old_cidr} left in place, safe)"

        if not old_cidr or not any(e.get('cidr') == old_cidr for e in entries):
            return True, f"ipset '{ipset}' now has {new_cidr} (no prior {old_cidr!r} entry to remove)"

        old_cidr_encoded = urllib.parse.quote(old_cidr, safe='')
        ok, output = self._run_proxmox_ssh_command(
            node, f"pvesh delete /cluster/firewall/ipset/{shlex.quote(ipset)}/{old_cidr_encoded}")
        if not ok:
            return False, (f"Added {new_cidr} but failed to remove old entry {old_cidr} — both are now "
                            f"present (safe, but the stale one should be cleaned up manually): {output}")

        return True, f"ipset '{ipset}' updated: {old_cidr} -> {new_cidr}"

    def _notify_proxmox_ipset_update(self, subnet_name, old_cidr, new_cidr, success, message):
        auth_config = _load_auth() or {}
        to_addr = auth_config.get('two_factor', {}).get('email', {}).get('to')
        status = 'succeeded' if success else 'FAILED'
        body = (
            f"Subnet '{subnet_name}''s live prefix changed, so dnsmasq-ui tried to update the "
            f"matching Proxmox cluster firewall ipset entry.\n\n"
            f"{old_cidr} -> {new_cidr}\nResult: {status}\nDetail: {message}\n\n"
            + ("" if success else
               "This does not block DNS tracking or the Group Update Plan's own interface-address "
               "pushes (which don't depend on this ipset) — but the ICMPv6/conntrack workaround this "
               "entry provides may be stale until fixed manually.\n\n") +
            f"Dashboard: {DASHBOARD_URL}/config"
        )
        if not to_addr:
            logger.error(f"Proxmox ipset update for subnet '{subnet_name}' {status}: {message} "
                         "— no notification email configured (enable email 2FA to set one)")
            return
        _send_email(to_addr, f"dnsmasq-ui: Proxmox ipset update {status} (subnet: {subnet_name})", body)

    def _notify_proxmox_ipset_ping_failure(self, subnet_name, failed_domains):
        """Email when the post-update ping6 check (see
        _verify_subnet_ping_reachability) still fails after an ipset
        CIDR update that itself succeeded — the ipset entry is correct
        and was kept (there's no better state to revert to), but the
        underlying connectivity issue it's meant to fix may not
        actually be resolved. Separate from _notify_proxmox_ipset_update
        since this is a distinct, later signal (checked only after the
        Group Update Plan has actually pushed the new addresses)."""
        auth_config = _load_auth() or {}
        to_addr = auth_config.get('two_factor', {}).get('email', {}).get('to')
        body = (
            f"Subnet '{subnet_name}''s Proxmox firewall ipset entry was updated successfully, but a "
            f"follow-up ping6 check still failed for {len(failed_domains)} host(s):\n\n" +
            "\n".join(f"  - {d}" for d in failed_domains) +
            "\n\nThe ipset entry itself is correct (nothing better to fall back to, so it was kept). "
            "This means the ICMPv6 ctstate-INVALID workaround this entry provides may not have taken "
            "effect this time — see project notes on this class of issue for troubleshooting steps "
            "(check pve-firewall's ip6tables counters directly, consider a firewall rule change "
            "anywhere in the cluster to force a recompile).\n\n"
            f"Dashboard: {DASHBOARD_URL}/config"
        )
        if not to_addr:
            logger.error(f"Post-update ping6 check for subnet '{subnet_name}' still failing for: "
                         f"{', '.join(failed_domains)} — no notification email configured "
                         "(enable email 2FA to set one)")
            return
        _send_email(to_addr, f"dnsmasq-ui: ping6 still failing after ipset update (subnet: {subnet_name})", body)

    def _propagate_subnet_prefix_change(self, subnet_name, old_prefix, new_prefix):
        """When poll_subnets() finds a subnet's live prefix has actually
        changed (not just been detected for the first time), first
        update any tracked Proxmox cluster ipset CIDR entry for this
        subnet (see update_proxmox_ipset_cidr) — deliberately BEFORE
        pushing any interface address changes below, so the firewall/
        conntrack path for the new addresses is already correct by the
        time they're validated, not fixed up after the fact. Then
        compute each affected update-group member's new target address
        and run that group through its Group Update Plan
        (run_update_group). run_update_group has other callers too — a
        future non-subnet-triggered source (e.g. the still-on-hold
        opnsense DHCPv6/RA drift work) would compute its own targets and
        call it directly instead of going through here.

        Finally, AFTER the group pushes (not right after the ipset
        update) — deliberately last, since the new addresses don't
        exist on any interface yet at the point the ipset gets updated,
        so a ping check there would just fail for the wrong reason —
        verifies via ping6 that the ipset change actually fixed the
        ICMPv6 issue it was meant to address (see
        _verify_subnet_ping_reachability). This is purely a follow-up
        diagnostic on top of the ipset update itself, which already
        reported its own success/failure via _notify_proxmox_ipset_update
        — a failure here doesn't undo anything (there's no better ipset
        state to fall back to), it's reported separately."""
        logger.warning(f"Subnet '{subnet_name}' prefix changed: {old_prefix} -> {new_prefix}")

        ipset_tracking = self.get_proxmox_ipset_tracking().get(subnet_name)
        if ipset_tracking:
            old_cidr = ipset_tracking.get('cidr') or old_prefix
            ok, msg = self.update_proxmox_ipset_cidr(
                ipset_tracking['ipset'], old_cidr, new_prefix, ipset_tracking.get('label', subnet_name))
            logger.warning(f"Proxmox ipset tracking for subnet '{subnet_name}': {'OK' if ok else 'FAILED'} — {msg}")
            if ok:
                ipset_tracking['cidr'] = new_prefix
                self.save_config()
            self._notify_proxmox_ipset_update(subnet_name, old_cidr, new_prefix, ok, msg)

        for group_name, group in self.get_update_groups().items():
            member_targets = {}
            for domain in group.get('members', []):
                entry = self.get_dynamic_host(domain)
                if not entry or entry.get('subnet') != subnet_name or entry.get('ipv6_host') is None:
                    continue
                try:
                    member_targets[domain] = str(
                        _ipv6_from_prefix_and_host(ipaddress.ip_network(new_prefix), entry['ipv6_host']))
                except ValueError as e:
                    logger.error(f"Failed computing new address for {domain}: {e}")
            if member_targets:
                self.run_update_group(group_name, member_targets)

        if ipset_tracking:
            ping_ok, ping_details = self._verify_subnet_ping_reachability(subnet_name)
            if ping_details:
                logger.warning(f"Post-update ping6 verification for subnet '{subnet_name}': "
                              f"{'OK' if ping_ok else 'FAILED'} — {ping_details}")
                if not ping_ok:
                    failed = [d['domain'] for d in ping_details if not d['ok']]
                    self._notify_proxmox_ipset_ping_failure(subnet_name, failed)

    def sync_update_group(self, group_name):
        """Manually run a group through its Group Update Plan right now,
        using each member's CURRENTLY expected target (computed from its
        subnet's live prefix_v6, same math as
        _propagate_subnet_prefix_change) rather than waiting for an
        actual detected prefix change. Useful to bring a newly-added
        member into compliance — e.g. provisioning IPv6 on a Proxmox
        node that's never had any — without needing to wait for, or
        fake, a real ISP renumbering event.

        Returns the same (all_converged, results) shape as
        run_update_group, or (False, [...]) with a single explanatory
        entry if the group doesn't exist or none of its members
        currently have a subnet+ipv6_host to compute a target from.
        """
        groups = self.get_update_groups()
        group = groups.get(group_name)
        if not group:
            return False, [{'domain': None, 'ok': False, 'detail': f"Unknown update group '{group_name}'"}]

        subnets = self.get_subnets()
        member_targets = {}
        for domain in group.get('members', []):
            entry = self.get_dynamic_host(domain)
            if not entry or entry.get('ipv6_host') is None:
                continue
            subnet = subnets.get(entry.get('subnet'), {})
            prefix_v6 = subnet.get('prefix_v6')
            if not prefix_v6:
                continue
            try:
                member_targets[domain] = str(
                    _ipv6_from_prefix_and_host(ipaddress.ip_network(prefix_v6), entry['ipv6_host']))
            except ValueError as e:
                logger.error(f"Failed computing sync-now target for {domain}: {e}")

        if not member_targets:
            return False, [{'domain': None, 'ok': False,
                             'detail': "No members with a subnet live prefix_v6 and ipv6_host to sync"}]

        return self.run_update_group(group_name, member_targets)

    def _build_vlan_netplan(self, vlan_id, ipv4_mode, ipv4_address, ipv6_mode, ipv6_address):
        """Netplan v2 YAML for a single VLAN sub-interface. ipv4_mode:
        'none'|'dhcp'|'static'. ipv6_mode: 'none'|'slaac'|'static'.
        Static addresses are pre-validated by the caller (add/update
        methods below) — this only formats them."""
        lines = [
            "network:",
            "  version: 2",
            "  vlans:",
            f"    eth0.{vlan_id}:",
            f"      id: {vlan_id}",
            "      link: eth0",
        ]
        addresses = []
        if ipv4_mode == 'dhcp':
            lines.append("      dhcp4: true")
        elif ipv4_mode == 'static':
            addresses.append(ipv4_address)
        if ipv6_mode == 'slaac':
            lines.append("      accept-ra: true")
        elif ipv6_mode == 'static':
            addresses.append(ipv6_address)
        if addresses:
            lines.append("      addresses:")
            lines.extend(f"        - {a}" for a in addresses)
        return "\n".join(lines) + "\n"

    def _validate_vlan_fields(self, vlan_id, name, ipv4_mode, ipv4_address, ipv6_mode, ipv6_address):
        if not isinstance(vlan_id, int) or not (1 <= vlan_id <= 4094):
            return "vlan_id must be an integer between 1 and 4094"
        if not self._VLAN_NAME_RE.match(name or ''):
            return "name must be lowercase alphanumeric/hyphen/underscore, starting with a letter"
        if ipv4_mode not in ('none', 'dhcp', 'static'):
            return "ipv4_mode must be 'none', 'dhcp', or 'static'"
        if ipv4_mode == 'static':
            try:
                ipaddress.IPv4Interface(ipv4_address)
            except (ValueError, TypeError):
                return f"ipv4_address '{ipv4_address}' is not a valid IPv4 address/CIDR (e.g. 192.168.7.5/24)"
        if ipv6_mode not in ('none', 'slaac', 'static'):
            return "ipv6_mode must be 'none', 'slaac', or 'static'"
        if ipv6_mode == 'static':
            try:
                ipaddress.IPv6Interface(ipv6_address)
            except (ValueError, TypeError):
                return f"ipv6_address '{ipv6_address}' is not a valid IPv6 address/CIDR (e.g. 2605:4a80:b009:c100::5/64)"
        return None

    def get_server_vlans(self, server_name):
        server = self.get_servers().get(server_name, {})
        return server.get('vlans', [])

    def add_server_vlan(self, server_name, vlan_id, name, ipv4_mode='none', ipv4_address=None,
                         ipv6_mode='slaac', ipv6_address=None):
        """Create a persistent VLAN sub-interface on a server (netplan),
        giving it a real IPv4/IPv6 presence on another subnet without a
        second physical NIC — requires the underlying Proxmox bridge (or
        equivalent) to already be trunking that VLAN to the VM; this only
        handles the guest-OS side. Provisions immediately over SSH rather
        than waiting for the next Ansible run, so it actually takes effect
        when added from the Config page."""
        servers = self.get_servers()
        if server_name not in servers:
            return False, f"Unknown server '{server_name}'"
        error = self._validate_vlan_fields(vlan_id, name, ipv4_mode, ipv4_address, ipv6_mode, ipv6_address)
        if error:
            return False, error

        vlans = servers[server_name].setdefault('vlans', [])
        if any(v['vlan_id'] == vlan_id for v in vlans):
            return False, f"VLAN {vlan_id} already configured on {server_name}"
        if any(v['name'] == name for v in vlans):
            return False, f"A VLAN named '{name}' already exists on {server_name}"

        netplan = self._build_vlan_netplan(vlan_id, ipv4_mode, ipv4_address, ipv6_mode, ipv6_address)
        server_ip = servers[server_name]['ip']
        try:
            self._write_remote_root_file(server_ip, netplan, f"/etc/netplan/90-presence-{name}.yaml")
        except Exception as e:
            return False, f"Failed to write netplan config to {server_name}: {e}"
        ok, output = self._run_remote_root_command(server_ip, "netplan apply")
        if not ok:
            return False, f"netplan apply failed on {server_name}: {output}"

        vlans.append({
            'vlan_id': vlan_id, 'name': name,
            'ipv4_mode': ipv4_mode, 'ipv4_address': ipv4_address,
            'ipv6_mode': ipv6_mode, 'ipv6_address': ipv6_address
        })
        self.save_config()
        return True, "VLAN interface created"

    def update_server_vlan(self, server_name, vlan_id, **fields):
        """Update and re-provision an existing VLAN sub-interface. name
        isn't updatable (it's the netplan filename on disk) — remove and
        re-add if it needs to change."""
        servers = self.get_servers()
        if server_name not in servers:
            return False, f"Unknown server '{server_name}'"
        vlans = servers[server_name].get('vlans', [])
        entry = next((v for v in vlans if v['vlan_id'] == vlan_id), None)
        if not entry:
            return False, f"VLAN {vlan_id} not found on {server_name}"

        merged = {**entry, **{k: v for k, v in fields.items()
                               if k in ('ipv4_mode', 'ipv4_address', 'ipv6_mode', 'ipv6_address')}}
        error = self._validate_vlan_fields(vlan_id, entry['name'], merged['ipv4_mode'],
                                            merged['ipv4_address'], merged['ipv6_mode'], merged['ipv6_address'])
        if error:
            return False, error

        netplan = self._build_vlan_netplan(vlan_id, merged['ipv4_mode'], merged['ipv4_address'],
                                            merged['ipv6_mode'], merged['ipv6_address'])
        server_ip = servers[server_name]['ip']
        try:
            self._write_remote_root_file(server_ip, netplan, f"/etc/netplan/90-presence-{entry['name']}.yaml")
        except Exception as e:
            return False, f"Failed to update netplan config on {server_name}: {e}"
        ok, output = self._run_remote_root_command(server_ip, "netplan apply")
        if not ok:
            return False, f"netplan apply failed on {server_name}: {output}"

        entry.update(merged)
        self.save_config()
        return True, "VLAN interface updated"

    def delete_server_vlan(self, server_name, vlan_id):
        """Tear down a VLAN sub-interface: remove its netplan file, then
        explicitly delete the link itself. `netplan apply` alone doesn't
        reliably remove an already-created VLAN link just because its
        declaration disappeared — confirmed empirically (the interface
        stayed up after apply, only `ip link delete` actually removed
        it) — so this doesn't rely on netplan for that part."""
        servers = self.get_servers()
        if server_name not in servers:
            return False, f"Unknown server '{server_name}'"
        vlans = servers[server_name].get('vlans', [])
        entry = next((v for v in vlans if v['vlan_id'] == vlan_id), None)
        if not entry:
            return False, f"VLAN {vlan_id} not found on {server_name}"

        in_use = [name for name, s in self.get_subnets().items()
                  if s.get('primary_dns') == server_name and s.get('interface') == f"eth0.{vlan_id}"]
        if in_use:
            return False, f"Still referenced by subnet(s): {', '.join(in_use)} — change their interface/primary_dns first"

        server_ip = servers[server_name]['ip']
        ok, output = self._run_remote_root_command(server_ip, f"rm -f /etc/netplan/90-presence-{entry['name']}.yaml")
        if not ok:
            return False, f"Failed to remove netplan config on {server_name}: {output}"
        ok, output = self._run_remote_root_command(server_ip, "netplan apply")
        if not ok:
            return False, f"netplan apply failed on {server_name}: {output}"
        # Best-effort: ignore failure if the link is already gone (netplan
        # occasionally does clean it up) rather than erroring the whole
        # delete over it.
        self._run_remote_root_command(server_ip, f"ip link delete eth0.{vlan_id}")

        vlans.remove(entry)
        self.save_config()
        return True, "VLAN interface removed"

    def add_dynamic_host(self, domain, zone_name, target_host=None, interface='eth0',
                          record_type='AAAA', ssh_user=None, enabled=True,
                          connection='paramiko', ssh_extra_args=None,
                          detect_command=None, detect_regex=None,
                          cli_prompt_regex=None, enable_command=None,
                          enable_password_ref=None, logout_command='exit',
                          ssh_password_ref=None, detect_url=None, login_url=None,
                          subnet=None, mac_address=None, ipv4_host=None, ipv6_host=None,
                          proxmox_update=None,
                          login_fields=None, login_password_field=None,
                          login_password_transform='none', login_password_ref=None,
                          session_param_regex=None, session_param_name='session_id',
                          verify_tls=True):
        """Track a host whose address (e.g. DHCPv6-assigned) should be polled
        and kept in sync with its DNS record, selected explicitly per-host
        rather than applying to every record.

        connection: 'paramiko' (default), 'cli', 'docker', or 'http'.
        - 'cli' shells out to the host's own ssh binary instead of paramiko,
          for devices paramiko can't negotiate with.
        - 'docker' runs an interactive session inside a container with an
          older OpenSSH client (see docker/legacy-ssh/), for devices with
          SSH servers too old for even the host's own ssh, and whose CLI
          only supports interactive sessions rather than one-shot command
          execution (common on embedded switch CLIs).
        - 'http' fetches a device's web UI instead of using SSH at all, for
          devices (routers) that only expose status via HTML. Uses
          detect_url/login_url/etc. below instead of target_host+a command.
        ssh_extra_args: extra flags passed to ssh, for 'cli'/'docker'
        (e.g. ["-o", "HostKeyAlgorithms=+ssh-dss"]).
        detect_command/detect_regex: override the default `ip addr` based
        detection with an arbitrary command and a regex (first capture
        group, or whole match) to pull the address out of its output —
        needed for non-Linux CLIs like switches that don't have an
        `ip`/eth0-style interface to query. Required for 'docker'.
        cli_prompt_regex: regex matching the device's shell prompt, used by
        'docker' to know when a command has finished producing output.
        enable_command/enable_password_ref: for Cisco-style CLIs with a
        separate privileged mode (e.g. `enable`) needed before show
        commands work. enable_password_ref looks up the actual password
        from DEVICE_CREDENTIALS_FILE by key — never stored in zones.json.
        logout_command: sent to end the session cleanly (default 'exit').
        detect_url: page to fetch (after login, if configured) and run
        detect_regex against — required for 'http'.
        login_url/login_fields/login_password_field/login_password_transform/
        login_password_ref: if the page needs a login first. login_fields is
        the static form data to POST; login_password_field names which field
        gets the (possibly transformed) password; login_password_transform
        picks a known vendor-specific obfuscation scheme (see
        _PASSWORD_TRANSFORMS) since some embedded web UIs scramble the
        password client-side in JS rather than sending it plain.
        session_param_regex/session_param_name: some devices track the
        login session via a URL query parameter instead of a cookie —
        session_param_regex extracts it from the login response,
        session_param_name is the query param name to append to detect_url.
        verify_tls: set False for self-signed-cert devices.

        subnet/mac_address/ipv4_host/ipv6_host: the cheaper alternative to
        all of the above — for a device on a subnet with a primary_dns
        configured (see add_subnet), its address is computed from that
        subnet's live prefix instead of polling the device directly. For
        AAAA, exactly one of mac_address (SLAAC/EUI-64 derives the suffix
        from it) or ipv6_host (an explicit, manually-assigned suffix like
        '::11' — for devices like a Proxmox host that don't self-configure
        via SLAAC, addressed the same way the IPv6 VIP itself is) is
        required — see the subnet-tracking section in README.md. Note that
        ipv6_host only keeps the *DNS record* in sync with a drifting
        prefix; unlike a MAC-derived address, the device's own static
        interface config doesn't auto-follow the prefix and needs updating
        by hand (or via Ansible) if it ever rotates — same class of
        follow-up as the IPv6 VIP drift monitoring, UNLESS proxmox_update
        is also set: {"node": "pve01", "iface": "vlan7"}, a Proxmox VE
        node name (from PROXMOX_NODE_IPS) and one of its own type=vlan
        interfaces. When set, a detected prefix change on this entry's
        subnet also pushes the recomputed address to that interface via
        the Proxmox API (see update_proxmox_interface_v6) instead of only
        fixing DNS — only meaningful alongside ipv6_host, ignored
        otherwise. ipv4_host is an explicit host number for A. Mutually
        exclusive with target_host/connection/detect_command/etc — this
        bypasses per-device polling entirely.
        """
        if not self.get_zone(zone_name):
            return False, "Zone not found"

        for entry in self.config['dynamic_hosts']:
            if entry['domain'] == domain and entry['record_type'] == record_type:
                return False, "Already tracked"

        if subnet:
            if subnet not in self.get_subnets():
                return False, f"Unknown subnet '{subnet}'"
            if record_type == 'AAAA':
                if not mac_address and ipv6_host is None:
                    return False, "mac_address or ipv6_host is required for AAAA subnet tracking"
                if mac_address and ipv6_host is not None:
                    return False, "mac_address and ipv6_host are mutually exclusive"
                if ipv6_host is not None:
                    try:
                        ipaddress.IPv6Address(ipv6_host)
                    except ValueError:
                        return False, f"ipv6_host '{ipv6_host}' is not a valid IPv6 address/suffix"
            if record_type == 'A' and ipv4_host is None:
                return False, "ipv4_host is required for A subnet tracking"
            if proxmox_update is not None:
                if ipv6_host is None:
                    return False, "proxmox_update only applies alongside ipv6_host"
                if not isinstance(proxmox_update, dict) or not proxmox_update.get('node') or not proxmox_update.get('iface'):
                    return False, "proxmox_update must be an object with 'node' and 'iface'"
                if proxmox_update['node'] not in PROXMOX_NODE_IPS:
                    return False, f"Unknown Proxmox node '{proxmox_update['node']}' — not in PROXMOX_NODES/PROXMOX_IPS"
            self.config['dynamic_hosts'].append({
                'domain': domain,
                'zone': zone_name,
                'record_type': record_type,
                'subnet': subnet,
                'mac_address': mac_address,
                'ipv4_host': ipv4_host,
                'ipv6_host': ipv6_host,
                'proxmox_update': proxmox_update,
                'enabled': enabled,
                'last_checked': None,
                'last_value': None,
                'last_updated': None
            })
            self.save_config()
            return True, "Dynamic host added"

        if not target_host:
            return False, "target_host is required unless using subnet-based tracking"

        self.config['dynamic_hosts'].append({
            'domain': domain,
            'zone': zone_name,
            'record_type': record_type,
            'target_host': target_host,
            'interface': interface,
            'ssh_user': ssh_user,
            'enabled': enabled,
            'connection': connection,
            'ssh_extra_args': ssh_extra_args or [],
            'detect_command': detect_command,
            'detect_regex': detect_regex,
            'cli_prompt_regex': cli_prompt_regex or r'[>#]\s*$',
            'enable_command': enable_command,
            'enable_password_ref': enable_password_ref,
            'logout_command': logout_command,
            'ssh_password_ref': ssh_password_ref,
            'detect_url': detect_url,
            'login_url': login_url,
            'login_fields': login_fields or {},
            'login_password_field': login_password_field,
            'login_password_transform': login_password_transform,
            'login_password_ref': login_password_ref,
            'session_param_regex': session_param_regex,
            'session_param_name': session_param_name,
            'verify_tls': verify_tls,
            'last_checked': None,
            'last_value': None,
            'last_updated': None
        })
        self.save_config()
        return True, "Dynamic host added"

    def update_dynamic_host(self, domain, **fields):
        """Update fields on a tracked host."""
        allowed = ('target_host', 'interface', 'ssh_user', 'enabled', 'connection',
                   'ssh_extra_args', 'detect_command', 'detect_regex',
                   'cli_prompt_regex', 'enable_command', 'enable_password_ref',
                   'logout_command', 'ssh_password_ref', 'detect_url', 'login_url',
                   'login_fields', 'login_password_field', 'login_password_transform',
                   'login_password_ref', 'session_param_regex', 'session_param_name',
                   'verify_tls', 'subnet', 'mac_address', 'ipv4_host', 'ipv6_host', 'proxmox_update')
        for entry in self.config['dynamic_hosts']:
            if entry['domain'] == domain:
                entry.update({k: v for k, v in fields.items() if k in allowed})
                self.save_config()
                return True, "Dynamic host updated"
        return False, "Not found"

    def delete_dynamic_host(self, domain):
        """Stop tracking a host."""
        before = len(self.config['dynamic_hosts'])
        self.config['dynamic_hosts'] = [e for e in self.config['dynamic_hosts'] if e['domain'] != domain]
        if before == len(self.config['dynamic_hosts']):
            return False, f"'{domain}' was not tracked"
        self.save_config()
        return True, "Dynamic host removed"

    def _load_vault_file(self):
        """Load the raw credentials file. 'credentials' values are still
        Fernet-encrypted at this point — this does not require the vault to
        be unlocked, since key names and salt/verify aren't secret."""
        if not os.path.exists(DEVICE_CREDENTIALS_FILE):
            return {'salt': None, 'verify': None, 'credentials': {}}
        try:
            with open(DEVICE_CREDENTIALS_FILE, 'r') as f:
                data = json.load(f)
                data.setdefault('credentials', {})
                return data
        except Exception as e:
            logger.error(f"Failed to load device credentials file: {e}")
            return {'salt': None, 'verify': None, 'credentials': {}}

    def _save_vault_file(self, data):
        fd = os.open(DEVICE_CREDENTIALS_FILE, os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)
        with os.fdopen(fd, 'w') as f:
            json.dump(data, f, indent=2)
        self._sync_peer_state()

    def _derive_vault_key(self, password, salt):
        kdf = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt,
                          iterations=600000, backend=default_backend())
        return base64.urlsafe_b64encode(kdf.derive(password.encode()))

    def vault_initialized(self):
        """Whether an admin password has been set up for the credential vault."""
        return bool(self._load_vault_file().get('salt'))

    def vault_unlocked(self):
        """Whether the derived key is currently cached in memory. Cleared on
        process restart (and by lock_vault), so password-gated
        dynamic_hosts entries need the vault unlocked again from the
        Configuration page after every restart — the background poller has
        no way to prompt for it itself."""
        return self._vault_key is not None

    def init_vault(self, admin_password):
        """First-time setup: pick a new salt, derive a key from
        admin_password, and store a verification marker (not any actual
        credential) so future unlock attempts can be checked against it.
        Refuses to run again once a vault exists — that would silently
        orphan any credentials already encrypted under the old key."""
        if self.vault_initialized():
            return False, "Vault already initialized — use unlock instead"
        salt = os.urandom(16)
        key = self._derive_vault_key(admin_password, salt)
        verify_token = Fernet(key).encrypt(b'dnsmasq-ui-vault-ok')
        self._save_vault_file({
            'salt': base64.b64encode(salt).decode(),
            'verify': verify_token.decode(),
            'credentials': {}
        })
        self._vault_key = key
        return True, "Vault initialized and unlocked"

    def unlock_vault(self, admin_password):
        """Derive the key from the stored salt and check it against the
        stored marker; cache it in memory (never written to disk) on
        success."""
        data = self._load_vault_file()
        if not data.get('salt'):
            return False, "Vault not initialized yet"
        salt = base64.b64decode(data['salt'])
        key = self._derive_vault_key(admin_password, salt)
        try:
            Fernet(key).decrypt(data['verify'].encode())
        except InvalidToken:
            return False, "Incorrect admin password"
        self._vault_key = key
        self._vault_lock_notified = False  # a future lock should send a fresh notice
        return True, "Vault unlocked"

    def lock_vault(self):
        """Drop the cached key from memory."""
        self._vault_key = None

    def _notify_vault_locked(self):
        """Email a heads-up that the vault is locked and password-gated
        polling is failing — notification only, not an unlock mechanism.
        The recipient still has to log in and enter the vault password
        normally; email compromise alone can't unlock anything. Sent at
        most once per lock (reset when unlock_vault succeeds) to avoid
        spamming every poll cycle."""
        auth_config = _load_auth() or {}
        to_addr = auth_config.get('two_factor', {}).get('email', {}).get('to')
        if not to_addr:
            logger.error("Vault is locked but no notification email address is configured (enable email 2FA to set one)")
            return
        sent, _ = _send_email(
            to_addr, 'dnsmasq-ui: credential vault is locked',
            "The device-credentials vault is locked (likely after a service restart), "
            "so password-gated dynamic host polling is failing.\n\n"
            f"Log in and unlock it from the Configuration page:\n{DASHBOARD_URL}/config\n\n"
            "This email is a notification only, it doesn't unlock anything by itself."
        )
        if sent:
            self._vault_lock_notified = True

    def check_ipv6_vip_drift(self):
        """Compare the configured IPv6 VIP's /64 against the active node's
        own real, currently-assigned /64 (RA/SLAAC on eth0). They should
        always match — the VIP's prefix is meant to track whatever subnet
        the active node is actually reachable on, kept static/manually
        managed rather than auto-rewritten (see README's IPv6 VIP section).
        If they've drifted apart (e.g. the ISP renumbered the delegated
        prefix), that's a real problem worth a human's attention: dnsmasq
        would still be answering fine, but the *IPv6 VIP itself* would be
        unreachable at its configured address. Returns (drifted, configured,
        actual) — notification and any actual reconfiguration is left to
        the caller/human, this only detects."""
        configured = self.config.get('global', {}).get('keepalive_vip6')
        if not configured:
            return False, None, None
        actual = _get_local_global_ipv6_prefix()
        if actual is None:
            return False, configured, None
        try:
            # keepalive_vip6 is always stored as a bare address (no
            # prefix) — see ansible/dnsmasq-setup.yml, which explicitly
            # appends /64 itself when building keepalived.conf. Without
            # forcing /64 here too, ip_interface(configured).network
            # silently defaults to /128 (a single-address "network"),
            # which can never equal actual's /64 — so drift was always
            # reported as True, confirmed live: every poll logged a
            # false-positive drift error, and the very first poll after
            # each process start emailed a spurious drift notice.
            configured_net = ipaddress.ip_network(f"{configured}/64", strict=False)
        except ValueError:
            return False, configured, str(actual)
        drifted = configured_net != actual
        return drifted, configured, str(actual)

    def _notify_ipv6_vip_drift(self, configured, actual):
        """Email a heads-up that the IPv6 VIP's configured prefix no longer
        matches the active node's real subnet — notification only, doesn't
        touch keepalived.conf itself. Sent at most once per drift episode
        (reset once the values match again) to avoid spamming every poll
        cycle."""
        auth_config = _load_auth() or {}
        to_addr = auth_config.get('two_factor', {}).get('email', {}).get('to')
        if not to_addr:
            logger.error(f"IPv6 VIP drift detected (configured {configured}, active node is actually on {actual}) "
                         "but no notification email address is configured (enable email 2FA to set one)")
            return
        sent, _ = _send_email(
            to_addr, 'dnsmasq-ui: IPv6 VIP prefix has drifted',
            f"The configured IPv6 VIP prefix ({configured}) no longer matches the subnet "
            f"the active DNS server is actually on ({actual}).\n\n"
            "This likely means the ISP renumbered the delegated IPv6 prefix. DNS itself is "
            "still working, but the IPv6 VIP address is no longer reachable at its "
            "configured address.\n\n"
            "This is a notification only — update keepalive_vip6 in zones.json's global "
            "config and the virtual_ipaddress in keepalived.conf on all DNS servers, then "
            f"restart keepalived.\n\nDashboard: {DASHBOARD_URL}/config"
        )
        if sent:
            self._v6_vip_drift_notified = True

    def _load_device_credentials(self):
        """Decrypt and return all stored device credentials as
        {key: plaintext_password}. Requires the vault to be unlocked;
        returns {} (with a logged error) if locked."""
        if not self.vault_unlocked():
            logger.error("Device credential vault is locked; unlock it from the Configuration page")
            return {}
        data = self._load_vault_file()
        fernet = Fernet(self._vault_key)
        result = {}
        for key, token in data.get('credentials', {}).items():
            try:
                result[key] = fernet.decrypt(token.encode()).decode()
            except InvalidToken:
                logger.error(f"Failed to decrypt stored credential '{key}' — vault key may not match")
        return result

    def set_device_credential(self, key, password):
        """Encrypt and store a device credential under `key`, referenced
        from a dynamic_hosts entry's enable_password_ref/ssh_password_ref —
        never stored in zones.json itself, which is committed to git.
        Requires the vault to be initialized and unlocked."""
        if not self.vault_unlocked():
            return False, "Vault is locked — initialize or unlock it first"
        data = self._load_vault_file()
        token = Fernet(self._vault_key).encrypt(password.encode())
        data['credentials'][key] = token.decode()
        try:
            self._save_vault_file(data)
            return True, "Credential saved"
        except Exception as e:
            logger.error(f"Failed to save device credential: {e}")
            return False, str(e)

    def delete_device_credential(self, key):
        """Remove a stored device credential. Doesn't require the vault to
        be unlocked, since deleting doesn't need to decrypt anything."""
        data = self._load_vault_file()
        if key not in data.get('credentials', {}):
            return False, "Not found"
        del data['credentials'][key]
        self._save_vault_file(data)
        return True, "Credential removed"

    def list_device_credential_keys(self):
        """List credential key names — doesn't require the vault unlocked,
        since key names aren't encrypted, only values."""
        return sorted(self._load_vault_file().get('credentials', {}).keys())

    def _ensure_legacy_ssh_image(self):
        """Build the legacy-ssh Docker image on first use if it's not
        already present. Returns True if the image is available."""
        check = subprocess.run(
            DOCKER_CMD + ['image', 'inspect', LEGACY_SSH_IMAGE],
            capture_output=True, timeout=10
        )
        if check.returncode == 0:
            return True
        logger.info(f"Building {LEGACY_SSH_IMAGE} image (first use)...")
        build = subprocess.run(
            DOCKER_CMD + ['build', '-t', LEGACY_SSH_IMAGE, LEGACY_SSH_DOCKERFILE_DIR],
            capture_output=True, text=True, timeout=300
        )
        if build.returncode != 0:
            logger.error(f"Failed to build {LEGACY_SSH_IMAGE}: {build.stderr}")
            return False
        return True

    def _run_ssh_docker(self, target_host, command, ssh_user=None, extra_args=None,
                         prompt_regex=r'[>#]\s*$', enable_command=None,
                         enable_password_ref=None, logout_command='exit',
                         ssh_password_ref=None):
        """Run an interactive CLI session against a device inside a
        container with an older OpenSSH client, for devices whose SSH
        server can't be reached at all from the host (e.g. old switches
        with DSA host keys outside modern OpenSSL's accepted parameter
        sizes) AND that only support an interactive terminal session
        rather than one-shot command execution (common on embedded switch
        CLIs) — so plain `ssh host command` doesn't work even once the
        crypto negotiates.

        Drives login (answering a password prompt if the device doesn't
        accept our key and ssh_password_ref is set) → optional
        privileged-mode step (e.g. Cisco-style `enable`, with its own
        optional password) → the actual command → logout, via a generated
        expect script piped into the container. Both passwords are looked
        up from DEVICE_CREDENTIALS_FILE by their *_ref key — never stored
        in zones.json. Returns the command's captured output, or None on
        failure.
        """
        if not self._ensure_legacy_ssh_image():
            return None

        creds = self._load_device_credentials()
        enable_password = creds.get(enable_password_ref) if enable_password_ref else None
        ssh_password = creds.get(ssh_password_ref) if ssh_password_ref else None

        # expect script is static; all variable data flows in via env vars
        # (not string-interpolated into the TCL source) to avoid needing to
        # escape untrusted-shaped config data for TCL syntax. BatchMode is
        # deliberately left off (unlike _run_ssh_cli) so a password prompt
        # actually appears instead of being auto-rejected.
        expect_script = r'''
set timeout 15
log_user 0
set prompt_re $env(DH_PROMPT_RE)

spawn sh -c "ssh -tt -o ConnectTimeout=8 -o StrictHostKeyChecking=accept-new -o NumberOfPasswordPrompts=1 $env(DH_SSH_ARGS) -i /key $env(DH_USER)@$env(DH_TARGET)"

expect {
    timeout { puts "===ERROR==="; puts "timed out waiting for login prompt"; exit 1 }
    eof { puts "===ERROR==="; puts "connection closed before login prompt"; exit 1 }
    -re {[Pp]assword:} {
        if {[info exists env(DH_SSH_PW)] && $env(DH_SSH_PW) ne ""} {
            send "$env(DH_SSH_PW)\r"
            exp_continue
        } else {
            puts "===ERROR==="; puts "device asked for a login password but none is configured (ssh_password_ref)"; exit 1
        }
    }
    -re $prompt_re { }
}

if {[info exists env(DH_ENABLE_CMD)] && $env(DH_ENABLE_CMD) ne ""} {
    send "$env(DH_ENABLE_CMD)\r"
    expect {
        timeout { puts "===ERROR==="; puts "timed out after enable command"; exit 1 }
        -re {[Pp]assword} {
            send "$env(DH_ENABLE_PW)\r"
            expect -re $prompt_re
        }
        -re $prompt_re { }
    }
}

send "$env(DH_DETECT_CMD)\r"
expect {
    timeout { puts "===ERROR==="; puts "timed out waiting for command output"; exit 1 }
    -re $prompt_re { }
}
puts "===OUTPUT_START==="
puts $expect_out(buffer)
puts "===OUTPUT_END==="

send "$env(DH_LOGOUT_CMD)\r"
expect eof
'''

        dh_vars = {
            'DH_TARGET': target_host,
            'DH_USER': ssh_user or SSH_USER,
            'DH_SSH_ARGS': ' '.join(extra_args or []),
            'DH_PROMPT_RE': prompt_regex,
            'DH_DETECT_CMD': command,
            'DH_LOGOUT_CMD': logout_command,
        }
        if enable_command:
            dh_vars['DH_ENABLE_CMD'] = enable_command
            dh_vars['DH_ENABLE_PW'] = enable_password or ''
        if ssh_password:
            dh_vars['DH_SSH_PW'] = ssh_password

        # sudo resets the environment by default, so subprocess.run(env=...)
        # alone won't carry these through to docker — pass them as VAR=value
        # arguments to sudo itself, which it explicitly honors per-command.
        sudo_env_args = [f"{k}={v}" for k, v in dh_vars.items()]
        docker_args = (
            [SUDO_BIN, '-n'] + sudo_env_args + [DOCKER_BIN] +
            ['run', '--rm', '-i', '--network', 'host', '-v', f'{SSH_KEY}:/key:ro']
        )
        for k in dh_vars:
            docker_args += ['-e', k]
        docker_args.append(LEGACY_SSH_IMAGE)

        try:
            result = subprocess.run(
                docker_args, input=expect_script, capture_output=True, text=True,
                timeout=45
            )
            output = result.stdout
            if '===ERROR===' in output:
                logger.error(f"Legacy SSH session to {target_host} failed: {output.split('===ERROR===', 1)[1].strip()}")
                return None
            if '===OUTPUT_START===' not in output:
                logger.error(
                    f"Legacy SSH session to {target_host} produced no captured output. "
                    f"stdout={output!r} stderr={result.stderr!r}"
                )
                return None
            return output.split('===OUTPUT_START===', 1)[1].split('===OUTPUT_END===', 1)[0]
        except Exception as e:
            logger.error(f"Legacy SSH session to {target_host} failed: {e}")
            return None

    def _run_ssh_paramiko(self, target_host, command, ssh_user=None):
        """Run command over SSH via paramiko. Returns stdout text, or None on failure."""
        try:
            ssh = paramiko.SSHClient()
            ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            ssh.connect(target_host, username=ssh_user or SSH_USER, key_filename=SSH_KEY, timeout=5)
            stdin, stdout, stderr = ssh.exec_command(command)
            output = stdout.read().decode()
            ssh.close()
            return output
        except Exception as e:
            logger.error(f"paramiko SSH to {target_host} failed: {e}")
            return None

    def _run_ssh_cli(self, target_host, command, ssh_user=None, extra_args=None):
        """Run command over SSH via the system ssh binary. Used for devices
        paramiko can't negotiate with (e.g. old switches with DSA host keys
        outside paramiko's supported key sizes) — the system ssh client
        doesn't share that limitation. Returns stdout text, or None on failure."""
        ssh_args = [
            SSH_BIN, '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=5',
            '-o', 'StrictHostKeyChecking=accept-new', '-i', SSH_KEY
        ]
        ssh_args += extra_args or []
        ssh_args.append(f"{ssh_user or SSH_USER}@{target_host}")
        ssh_args.append(command)
        try:
            result = subprocess.run(ssh_args, capture_output=True, text=True, timeout=15)
            if result.returncode != 0:
                logger.error(f"ssh CLI to {target_host} exited {result.returncode}: {result.stderr.strip()}")
                return None
            return result.stdout
        except Exception as e:
            logger.error(f"ssh CLI to {target_host} failed: {e}")
            return None

    def _run_http_scrape(self, entry):
        """Fetch a device's web UI (optionally logging in first) and return
        the page text for detect_regex to run against — for routers/devices
        that only expose status via HTML, no SSH/CLI at all.

        Login (if login_url is set) is fully vendor-specific — embedded web
        UIs commonly obfuscate the password client-side in JS before POSTing
        (not real security, just obscurity) and track the session via a URL
        query parameter instead of a cookie, as validated against a real
        Linksys E5400. login_password_transform picks which known scheme to
        replicate; add new entries to _PASSWORD_TRANSFORMS as new devices are
        validated rather than trying to guess a device's scheme blind.
        """
        verify_tls = entry.get('verify_tls', True)
        ctx = None
        if not verify_tls:
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE

        detect_url = entry.get('detect_url')
        if not detect_url:
            logger.error(f"http connection for {entry.get('domain')} needs detect_url")
            return None

        login_url = entry.get('login_url')
        if login_url:
            password = None
            if entry.get('login_password_ref'):
                password = self._load_device_credentials().get(entry['login_password_ref'])
            transform_name = entry.get('login_password_transform', 'none')
            transform = _PASSWORD_TRANSFORMS.get(transform_name)
            if not transform:
                logger.error(f"Unknown login_password_transform '{transform_name}' for {entry.get('domain')}")
                return None

            fields = dict(entry.get('login_fields') or {})
            if entry.get('login_password_field') and password is not None:
                fields[entry['login_password_field']] = transform(password)

            try:
                body = urllib.parse.urlencode(fields).encode()
                req = urllib.request.Request(
                    login_url, data=body, method='POST',
                    headers={'Content-Type': 'application/x-www-form-urlencoded'}
                )
                with urllib.request.urlopen(req, timeout=10, context=ctx) as resp:
                    login_response_text = resp.read().decode('utf-8', errors='replace')
            except Exception as e:
                logger.error(f"Login to {login_url} failed for {entry.get('domain')}: {e}")
                return None

            session_regex = entry.get('session_param_regex')
            if session_regex:
                match = re.search(session_regex, login_response_text)
                if not match:
                    logger.error(f"Login for {entry.get('domain')} succeeded but session_param_regex found nothing")
                    return None
                session_value = match.group(1) if match.groups() else match.group(0)
                param_name = entry.get('session_param_name', 'session_id')
                sep = '&' if '?' in detect_url else '?'
                detect_url = f"{detect_url}{sep}{param_name}={session_value}"

        try:
            req = urllib.request.Request(detect_url, method='GET')
            with urllib.request.urlopen(req, timeout=10, context=ctx) as resp:
                return resp.read().decode('utf-8', errors='replace')
        except Exception as e:
            logger.error(f"Fetching {detect_url} failed for {entry.get('domain')}: {e}")
            return None

    def test_dynamic_host(self, entry):
        """Dry-run address detection for an entry (saved or not), returning
        full debug info — command run, raw output, and either the detected
        address or the reason detection failed. Meant for iterating on
        detect_command/detect_regex against an unfamiliar device CLI (e.g. a
        switch) before committing to a saved dynamic_hosts entry.
        """
        if entry.get('subnet'):
            address = self._detect_subnet_member_address(entry)
            result = {'command': f"(computed from subnet '{entry['subnet']}')", 'connection': 'subnet'}
            if address:
                result['detected_address'] = address
            else:
                subnet_cfg = self.get_subnets().get(entry['subnet'])
                if not subnet_cfg:
                    result['error'] = f"Unknown subnet '{entry['subnet']}'"
                elif entry.get('record_type', 'AAAA') == 'AAAA' and not subnet_cfg.get('prefix_v6'):
                    result['error'] = "Subnet has no live IPv6 prefix yet -- run a poll first (needs primary_dns configured and reachable)"
                else:
                    result['error'] = 'Address computation failed -- check mac_address/ipv4_host and server logs'
            return result

        target_host = entry.get('target_host')
        ssh_user = entry.get('ssh_user')
        connection = entry.get('connection', 'paramiko')
        record_type = entry.get('record_type', 'AAAA')
        detect_command = entry.get('detect_command')
        detect_regex = entry.get('detect_regex')

        if connection == 'http':
            # No SSH "command" concept here — detect_url is what gets
            # fetched (post-login, if configured), and detect_regex is
            # always required since there's no generic "default" way to
            # find an address in an arbitrary HTML page the way `ip addr`
            # works as a Linux default.
            command = entry.get('detect_url', '')
            output = self._run_http_scrape(entry)
        else:
            if detect_command:
                command = detect_command
            else:
                interface = entry.get('interface', 'eth0')
                if record_type == 'AAAA':
                    command = (
                        f"ip -6 -o addr show {interface} scope global | "
                        "grep -v temporary | awk '{print $4}' | cut -d/ -f1 | head -1"
                    )
                else:
                    command = f"ip -4 -o addr show {interface} scope global | awk '{{print $4}}' | cut -d/ -f1 | head -1"

            if connection == 'docker':
                output = self._run_ssh_docker(
                    target_host, command, ssh_user, entry.get('ssh_extra_args'),
                    entry.get('cli_prompt_regex', r'[>#]\s*$'),
                    entry.get('enable_command'), entry.get('enable_password_ref'),
                    entry.get('logout_command', 'exit'), entry.get('ssh_password_ref')
                )
            elif connection == 'cli':
                output = self._run_ssh_cli(target_host, command, ssh_user, entry.get('ssh_extra_args'))
            else:
                output = self._run_ssh_paramiko(target_host, command, ssh_user)

        result = {'command': command, 'connection': connection, 'raw_output': output}

        if output is None:
            result['error'] = (
                'HTTP request failed — see server logs for details' if connection == 'http'
                else 'SSH command failed — see server logs for details'
            )
            return result

        if connection == 'http' or detect_command:
            if not detect_regex:
                result['error'] = 'detect_regex is required for this connection type; refusing to guess'
                return result
            match = re.search(detect_regex, output)
            if not match:
                result['error'] = 'detect_regex did not match the command output'
                return result
            address = match.group(1) if match.groups() else match.group(0)
        else:
            address = output.strip()

        if not address:
            result['error'] = 'no address extracted'
            return result

        # Sanity-check it's actually a valid address of the expected family
        # before letting it anywhere near a DNS record.
        try:
            parsed = ipaddress.ip_address(address)
        except ValueError:
            result['error'] = f"'{address}' is not a valid IP address"
            return result
        if (record_type == 'AAAA') != (parsed.version == 6):
            result['error'] = f"'{address}' is IPv{parsed.version}, expected {record_type}"
            return result

        result['detected_address'] = address
        return result

    def _detect_current_address(self, entry):
        """Read a tracked host's current address per its entry config.
        Returns the address string, or None if detection failed.
        """
        if entry.get('subnet'):
            return self._detect_subnet_member_address(entry)
        return self.test_dynamic_host(entry).get('detected_address')

    def poll_subnets(self):
        """Refresh each configured subnet's live IPv6 prefix from its
        primary_dns server — one SSH round-trip per subnet, not per
        device. Subnet-tracked dynamic_hosts entries (a 'subnet' field
        instead of target_host/detect_command) then compute their own
        address from this prefix plus their own MAC (EUI-64), rather than
        being individually polled every cycle. Devices without a stable,
        directly-reachable member of their own subnet to reference (e.g.
        wifi.mgmt.alshowto.com, the only thing on 192.168.7.0/24 we can
        reach) stay on the old per-device polling instead."""
        subnets = self.config.get('global', {}).get('subnets', {})
        servers = self.get_servers()
        for name, subnet in subnets.items():
            primary_dns = subnet.get('primary_dns')
            reference_host = subnet.get('reference_host')
            ip = servers[primary_dns]['ip'] if primary_dns in servers else reference_host
            if not ip:
                logger.error(f"Subnet '{name}' has no primary_dns or reference_host configured — skipping prefix detection")
                continue
            interface = subnet.get('interface', 'eth0')
            output = self._run_ssh_paramiko(ip, f'ip -6 -o addr show {interface} scope global dynamic', None)
            if not output:
                logger.error(f"Subnet '{name}': failed to detect IPv6 prefix via {ip} ({interface})")
                continue
            match = re.search(r'inet6 ([0-9a-fA-F:]+)/', output)
            if not match:
                logger.error(f"Subnet '{name}': no dynamic global IPv6 address found on {ip}")
                continue
            old_prefix = subnet.get('prefix_v6')
            try:
                new_prefix = str(ipaddress.ip_interface(f"{match.group(1)}/64").network)
            except ValueError:
                logger.error(f"Subnet '{name}': '{match.group(1)}' is not a valid address")
                continue
            subnet['prefix_v6'] = new_prefix
            if old_prefix and new_prefix != old_prefix:
                self._propagate_subnet_prefix_change(name, old_prefix, new_prefix)

    def _detect_subnet_member_address(self, entry):
        """Compute a subnet-tracked device's current address from its
        subnet's live prefix/CIDR plus its own MAC (AAAA, SLAAC/EUI-64),
        an explicit static suffix (AAAA, ipv6_host), or host number (A) —
        no per-device polling needed, see poll_subnets()."""
        subnet = self.config.get('global', {}).get('subnets', {}).get(entry.get('subnet'))
        if not subnet:
            logger.error(f"'{entry['domain']}' references unknown subnet '{entry.get('subnet')}'")
            return None
        record_type = entry.get('record_type', 'AAAA')
        try:
            if record_type == 'AAAA':
                prefix_v6 = subnet.get('prefix_v6')
                if not prefix_v6:
                    return None
                mac = entry.get('mac_address')
                ipv6_host = entry.get('ipv6_host')
                if mac:
                    return str(_ipv6_from_prefix_and_mac(ipaddress.ip_network(prefix_v6), mac))
                elif ipv6_host is not None:
                    return str(_ipv6_from_prefix_and_host(ipaddress.ip_network(prefix_v6), ipv6_host))
                return None
            else:
                cidr_v4, host_number = subnet.get('cidr_v4'), entry.get('ipv4_host')
                if not cidr_v4 or host_number is None:
                    return None
                return str(_ipv4_from_cidr_and_host(cidr_v4, host_number))
        except ValueError as e:
            logger.error(f"Subnet address computation failed for {entry['domain']}: {e}")
            return None

    def poll_dynamic_hosts(self):
        """Check every enabled tracked host for an address change and, if
        changed, update its DNS record and redeploy once at the end.

        Returns:
            dict: {'changes': {domain: {...}}, 'deployed': bool}
        """
        self.poll_subnets()

        results = {}
        changed_any = False

        entries = [e for e in self.config.get('dynamic_hosts', []) if e.get('enabled', True)]
        needs_vault = any(
            e.get('enable_password_ref') or e.get('ssh_password_ref') or e.get('login_password_ref')
            for e in entries
        )
        if needs_vault and not self.vault_unlocked() and not self._vault_lock_notified:
            self._notify_vault_locked()

        for entry in entries:
            domain = entry['domain']
            record_type = entry.get('record_type', 'AAAA')
            zone = self.get_zone(entry['zone'])
            if not zone:
                results[domain] = {'changed': False, 'error': 'zone not found'}
                continue

            current = self._detect_current_address(entry)
            entry['last_checked'] = datetime.now().isoformat()

            if not current:
                results[domain] = {'changed': False, 'error': 'detection failed'}
                continue

            existing = next((r['value'] for r in zone['records']
                              if r['domain'] == domain and r['type'] == record_type), None)

            if current == existing:
                results[domain] = {'changed': False, 'value': current}
            else:
                # add_record()/update_record() sync the PTR record
                # themselves now (same A/AAAA-only logic this used to
                # call explicitly here) -- no separate call needed.
                if existing is None:
                    self.add_record(entry['zone'], domain, record_type, current)
                else:
                    self.update_record(entry['zone'], domain, record_type, current)
                entry['last_updated'] = datetime.now().isoformat()
                results[domain] = {'changed': True, 'old': existing, 'new': current}
                changed_any = True

            entry['last_value'] = current

        self.save_config()
        if changed_any:
            self.deploy_to_servers()

        return {'changes': results, 'deployed': changed_any}

    def _sync_ptr_record(self, zone, forward_domain, old_address, new_address):
        """Keep an A/AAAA record's PTR record in sync with it. Called from
        add_record()/update_record()/delete_record() (any A/AAAA change,
        including manual edits via Zone Management) and from
        poll_dynamic_hosts() indirectly, through those same two methods --
        so this is the one place reverse DNS gets maintained regardless
        of what triggered the forward-record change. Removes the PTR at
        the old address (if any -- None on a brand-new record or a
        deletion with nothing to add) and (re)creates it at the new one
        (None on a deletion, nothing to add). Mutates zone['records'] in
        place and does not call save_config() itself; the caller already
        does its own save right after, so this rides along with that
        instead of costing a second peer-sync SSH round trip.

        old_address/new_address aren't guaranteed to be real IPs -- e.g.
        a failed dynamic_hosts detection upstream, or arbitrary API input
        -- so a value that doesn't parse is treated as "nothing to do"
        for that side rather than raised, same as the other record
        types' malformed-input handling elsewhere in
        generate_dnsmasq_config()."""
        if old_address:
            try:
                old_ptr_name = ipaddress.ip_address(old_address).reverse_pointer
            except (ValueError, TypeError):
                old_ptr_name = None
            if old_ptr_name:
                zone['records'] = [r for r in zone['records']
                                    if not (r['domain'] == old_ptr_name and r['type'] == 'PTR'
                                            and r['value'] == forward_domain)]

        if new_address:
            try:
                new_ptr_name = ipaddress.ip_address(new_address).reverse_pointer
            except (ValueError, TypeError):
                return
            # Replace rather than duplicate, in case a stale PTR for this
            # exact (name, forward_domain) pair is already sitting there
            # (e.g. a re-poll that lands back on a previously-seen address).
            zone['records'] = [r for r in zone['records']
                                if not (r['domain'] == new_ptr_name and r['type'] == 'PTR'
                                        and r['value'] == forward_domain)]
            zone['records'].append({'domain': new_ptr_name, 'type': 'PTR', 'value': forward_domain})

    def backfill_ptr_records(self):
        """One-time reconciliation: ensure every existing A/AAAA record
        across every zone has a matching PTR record. Only needed for
        records that predate PTR auto-sync (add_record()/update_record()
        already keep new/changed ones current going forward) -- safe to
        re-run at any time regardless, since _sync_ptr_record() replaces
        rather than duplicates. Returns the number of A/AAAA records
        processed."""
        count = 0
        for zone in self.get_zones():
            for record in list(zone['records']):
                if record['type'] in ('A', 'AAAA'):
                    self._sync_ptr_record(zone, record['domain'], None, record['value'])
                    count += 1
        self.save_config()
        return count

    def get_ssh_key_info(self):
        """Get current SSH key information."""
        try:
            if not os.path.exists(SSH_KEY):
                return {'exists': False}

            # Get file info
            stat = os.stat(SSH_KEY)

            # Load key and get fingerprint
            pkey = paramiko.RSAKey.from_private_key_file(SSH_KEY)
            # Get MD5 fingerprint of the public key (base64 data)
            base64_key = pkey.get_base64()
            if isinstance(base64_key, str):
                base64_key = base64_key.encode()
            fingerprint = hashlib.md5(base64_key).hexdigest()
            fingerprint_hex = ':'.join([fingerprint[i:i+2] for i in range(0, len(fingerprint), 2)])

            return {
                'exists': True,
                'path': SSH_KEY,
                'size': stat.st_size,
                'modified': datetime.fromtimestamp(stat.st_mtime).isoformat(),
                'fingerprint': fingerprint_hex,
                'key_type': 'RSA',
                'bits': pkey.get_bits(),
                'public_key': f"{pkey.get_name()} {pkey.get_base64()} dnsmasq-ui"
            }
        except Exception as e:
            logger.error(f"Error reading SSH key: {str(e)}")
            return {'exists': False, 'error': str(e)}

    def generate_ssh_key(self):
        """Generate new RSA SSH key pair."""
        try:
            # Generate private key
            private_key = rsa.generate_private_key(
                public_exponent=65537,
                key_size=4096,
                backend=default_backend()
            )

            # Serialize private key
            private_pem = private_key.private_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PrivateFormat.OpenSSH,
                encryption_algorithm=serialization.NoEncryption()
            )

            # Serialize public key
            public_key = private_key.public_key()
            public_pem = public_key.public_bytes(
                encoding=serialization.Encoding.OpenSSH,
                format=serialization.PublicFormat.OpenSSH
            )

            return {
                'success': True,
                'private_key': private_pem.decode(),
                'public_key': public_pem.decode()
            }
        except Exception as e:
            return {'success': False, 'error': str(e)}

    def _load_wg_keys(self):
        """Load WireGuard private keys from wireguard-keys.json.

        Returns:
            dict: Keys structure, or {} if file doesn't exist.
        """
        if not os.path.exists(WG_KEYS_FILE):
            return {}
        try:
            with open(WG_KEYS_FILE, 'r') as f:
                data = json.load(f)
                return data.get('keys', {})
        except Exception as e:
            logger.error(f"Failed to load WireGuard keys: {e}")
            return {}

    def _save_wg_keys(self, keys):
        """Save WireGuard private keys with secure 0600 permissions.

        Args:
            keys (dict): Private key data to save.
        """
        try:
            data = {
                'version': '1',
                'generated': datetime.now().isoformat(),
                'keys': keys
            }
            # Use os.open with 0o600 for atomic secure creation
            fd = os.open(WG_KEYS_FILE, os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)
            with os.fdopen(fd, 'w') as f:
                json.dump(data, f, indent=2)
            logger.info(f"WireGuard keys saved to {WG_KEYS_FILE} with 0600 permissions")
        except Exception as e:
            logger.error(f"Failed to save WireGuard keys: {e}")
            raise

    def generate_wg_keypair(self):
        """Generate X25519 WireGuard keypair.

        Returns:
            dict: {'private_key': base64_str, 'public_key': base64_str}
        """
        try:
            private_key = X25519PrivateKey.generate()
            private_bytes = private_key.private_bytes(
                encoding=serialization.Encoding.Raw,
                format=serialization.PrivateFormat.Raw,
                encryption_algorithm=serialization.NoEncryption()
            )
            public_bytes = private_key.public_key().public_bytes(
                encoding=serialization.Encoding.Raw,
                format=serialization.PublicFormat.Raw
            )
            return {
                'private_key': base64.b64encode(private_bytes).decode(),
                'public_key': base64.b64encode(public_bytes).decode()
            }
        except Exception as e:
            logger.error(f"Failed to generate WireGuard keypair: {e}")
            raise

    def _assign_wg_tunnel_ips(self):
        """Assign deterministic tunnel IPs to servers without them.

        Uses sorted server names for consistent ordering.
        Assigns IPs from mesh_subnet in global.wireguard config.
        """
        try:
            wg_config = self.config.get('global', {}).get('wireguard', {})
            mesh_subnet = wg_config.get('mesh_subnet', '10.99.0.0/24')
            network = ipaddress.ip_network(mesh_subnet)

            # Get sorted server names for deterministic ordering
            server_names = sorted(self.config.get('servers', {}).keys())

            # Assign IPs: skip .0 and .255 (network/broadcast)
            host_ips = list(network.hosts())

            for idx, server_name in enumerate(server_names):
                if idx >= len(host_ips):
                    logger.warning(f"Not enough IPs in {mesh_subnet} for server {server_name}")
                    break

                server = self.config['servers'][server_name]
                if 'wireguard' not in server:
                    server['wireguard'] = {}

                # Only assign if not already set
                if not server['wireguard'].get('tunnel_ip'):
                    tunnel_ip = str(host_ips[idx]) + '/24'
                    server['wireguard']['tunnel_ip'] = tunnel_ip
                    logger.info(f"Assigned {server_name} tunnel IP: {tunnel_ip}")
        except Exception as e:
            logger.error(f"Failed to assign WireGuard tunnel IPs: {e}")

    def generate_wg_keys_for_all_servers(self, overwrite=False):
        """Generate WireGuard keypairs for all enabled servers.

        Args:
            overwrite (bool): If True, regenerate keys for all servers.

        Returns:
            dict: {'generated': [...], 'skipped': [...], 'errors': {}}
        """
        try:
            result = {'generated': [], 'skipped': [], 'errors': {}}
            existing_keys = self._load_wg_keys()
            new_keys = dict(existing_keys)

            # Assign tunnel IPs first
            self._assign_wg_tunnel_ips()

            for server_name in sorted(self.config.get('servers', {}).keys()):
                server = self.config['servers'][server_name]

                if not server.get('enabled', True):
                    result['skipped'].append(server_name)
                    continue

                # Skip if already has keys and overwrite=False
                if server_name in existing_keys and not overwrite:
                    result['skipped'].append(server_name)
                    continue

                try:
                    # Generate new keypair
                    keypair = self.generate_wg_keypair()
                    new_keys[server_name] = {
                        'private_key': keypair['private_key'],
                        'generated': datetime.now().isoformat()
                    }

                    # Store public key in zones.json
                    if 'wireguard' not in server:
                        server['wireguard'] = {}
                    server['wireguard']['public_key'] = keypair['public_key']
                    server['wireguard']['generated'] = datetime.now().isoformat()

                    result['generated'].append(server_name)
                    logger.info(f"Generated WireGuard keys for {server_name}")
                except Exception as e:
                    result['errors'][server_name] = str(e)
                    logger.error(f"Failed to generate keys for {server_name}: {e}")

            # Save keys and config
            self._save_wg_keys(new_keys)
            self.save_config()

            return result
        except Exception as e:
            logger.error(f"Error in generate_wg_keys_for_all_servers: {e}")
            raise

    def generate_wg_config_for_server(self, server_name):
        """Generate wg0.conf content for a specific server.

        Args:
            server_name (str): Name of the server.

        Returns:
            tuple: (success: bool, config_or_error: str)
        """
        try:
            server = self.config['servers'].get(server_name)
            if not server:
                return False, f"Server {server_name} not found"

            # Load private key for this server
            wg_keys = self._load_wg_keys()
            if server_name not in wg_keys:
                return False, f"No private key found for {server_name}"

            private_key = wg_keys[server_name]['private_key']

            # Get interface config
            if 'wireguard' not in server:
                return False, f"No WireGuard config for {server_name}"

            tunnel_ip = server['wireguard'].get('tunnel_ip', f'10.99.0.{list(self.config["servers"].keys()).index(server_name) + 1}')
            listen_port = server['wireguard'].get('listen_port', self.config['global']['wireguard']['listen_port'])

            # Start config with Interface section
            config_lines = [
                f"# WireGuard mesh config for {server_name}",
                "# Generated by dnsmasq-ui",
                "",
                "[Interface]",
                f"Address = {tunnel_ip}",
                f"ListenPort = {listen_port}",
                f"PrivateKey = {private_key}",
                ""
            ]

            # Add peer entries for all other servers
            persistent_keepalive = self.config['global']['wireguard'].get('persistent_keepalive', 25)

            for peer_name in sorted(self.config['servers'].keys()):
                if peer_name == server_name:
                    continue  # Don't add self as peer

                peer = self.config['servers'][peer_name]
                if not peer.get('enabled', True):
                    continue

                if 'wireguard' not in peer or not peer['wireguard'].get('public_key'):
                    logger.warning(f"Skipping peer {peer_name}: no public key")
                    continue

                peer_public_key = peer['wireguard']['public_key']
                peer_tunnel_ip = peer['wireguard'].get('tunnel_ip', f'10.99.0.{list(self.config["servers"].keys()).index(peer_name) + 1}')
                peer_endpoint = f"{peer['ip']}:{peer['wireguard'].get('listen_port', listen_port)}"

                config_lines.extend([
                    f"# Peer: {peer_name}",
                    "[Peer]",
                    f"PublicKey = {peer_public_key}",
                    f"AllowedIPs = {peer_tunnel_ip.split('/')[0]}/32",
                    f"Endpoint = {peer_endpoint}",
                    f"PersistentKeepalive = {persistent_keepalive}",
                    ""
                ])

            return True, '\n'.join(config_lines)
        except Exception as e:
            logger.error(f"Failed to generate WireGuard config for {server_name}: {e}")
            return False, str(e)

    def validate_wg_config(self):
        """Validate WireGuard configuration completeness.

        Returns:
            dict: {'valid': bool, 'errors': [...], 'warnings': [...]}
        """
        result = {'valid': True, 'errors': [], 'warnings': []}

        try:
            wg_config = self.config.get('global', {}).get('wireguard', {})

            if not wg_config.get('enabled'):
                result['warnings'].append("WireGuard is disabled (enabled: false)")
                return result

            # Check mesh_subnet validity
            try:
                ipaddress.ip_network(wg_config.get('mesh_subnet', '10.99.0.0/24'))
            except Exception as e:
                result['valid'] = False
                result['errors'].append(f"Invalid mesh_subnet: {e}")

            # Check all servers have public keys and tunnel IPs
            for server_name, server in self.config.get('servers', {}).items():
                if not server.get('enabled', True):
                    continue

                wg = server.get('wireguard', {})
                if not wg.get('public_key'):
                    result['valid'] = False
                    result['errors'].append(f"{server_name}: missing public_key")
                if not wg.get('tunnel_ip'):
                    result['valid'] = False
                    result['errors'].append(f"{server_name}: missing tunnel_ip")

            # Check private keys file exists with all server keys
            wg_keys = self._load_wg_keys()
            for server_name in self.config.get('servers', {}).keys():
                server = self.config['servers'][server_name]
                if server.get('enabled', True) and server_name not in wg_keys:
                    result['valid'] = False
                    result['errors'].append(f"{server_name}: no private key in {WG_KEYS_FILE}")

            # Check tunnel IP collisions
            tunnel_ips = set()
            for server in self.config.get('servers', {}).values():
                if not server.get('enabled', True):
                    continue
                tunnel_ip = server.get('wireguard', {}).get('tunnel_ip')
                if tunnel_ip:
                    ip_only = tunnel_ip.split('/')[0]
                    if ip_only in tunnel_ips:
                        result['valid'] = False
                        result['errors'].append(f"Duplicate tunnel IP: {ip_only}")
                    tunnel_ips.add(ip_only)

        except Exception as e:
            result['valid'] = False
            result['errors'].append(f"Validation error: {e}")

        return result

    def generate_wg_dnsmasq_config(self, server_name):
        """Generate dnsmasq configuration for WireGuard interface.

        Args:
            server_name (str): Name of the server.

        Returns:
            str: dnsmasq config content for wg0 interface.
        """
        try:
            server = self.config['servers'].get(server_name)
            if not server or 'wireguard' not in server:
                return ""

            tunnel_ip = server['wireguard'].get('tunnel_ip', '10.99.0.1').split('/')[0]

            config = (
                "# dnsmasq-ui: WireGuard mesh DNS binding\n"
                "interface=wg0\n"
                f"listen-address={tunnel_ip}\n"
            )
            return config
        except Exception as e:
            logger.error(f"Failed to generate dnsmasq WireGuard config: {e}")
            return ""

    def deploy_wg_to_server(self, server_name):
        """Deploy WireGuard configuration to a single server via SSH.

        Uses base64 transport to avoid shell quoting issues with keys.

        Args:
            server_name (str): Name of the server.

        Returns:
            tuple: (success: bool, message: str)
        """
        try:
            server = self.config['servers'].get(server_name)
            if not server:
                return False, f"Server {server_name} not found"

            # Generate WireGuard config
            success, config_content = self.generate_wg_config_for_server(server_name)
            if not success:
                return False, f"Failed to generate config: {config_content}"

            # Encode config in base64 to avoid shell quoting issues
            config_b64 = base64.b64encode(config_content.encode()).decode()

            # SSH commands: install, create dirs, deploy config, set permissions, enable
            commands = ";".join([
                "sudo mkdir -p /etc/wireguard && sudo chmod 0700 /etc/wireguard",
                f"echo '{config_b64}' | base64 -d | sudo tee /etc/wireguard/wg0.conf > /dev/null",
                "sudo chmod 0600 /etc/wireguard/wg0.conf",
                "sudo wg-quick down wg0 2>/dev/null || true",
                "sudo wg-quick up wg0"
            ])

            ssh = paramiko.SSHClient()
            ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            ssh.connect(server['ip'], username=SSH_USER, key_filename=SSH_KEY, timeout=10)

            stdin, stdout, stderr = ssh.exec_command(commands)
            error = stderr.read().decode()
            ssh.close()

            if error and 'error' in error.lower():
                return False, f"SSH error: {error}"

            logger.info(f"WireGuard deployed to {server_name}")
            return True, f"WireGuard deployed to {server_name}"

        except Exception as e:
            logger.error(f"Failed to deploy WireGuard to {server_name}: {e}")
            return False, str(e)

    def deploy_wg_dnsmasq_config(self, server_name):
        """Deploy dnsmasq WireGuard binding configuration to a server.

        Args:
            server_name (str): Name of the server.

        Returns:
            tuple: (success: bool, message: str)
        """
        try:
            server = self.config['servers'].get(server_name)
            if not server:
                return False, f"Server {server_name} not found"

            # Generate dnsmasq config
            config_content = self.generate_wg_dnsmasq_config(server_name)
            if not config_content:
                return False, "No WireGuard config to deploy"

            # Encode in base64
            config_b64 = base64.b64encode(config_content.encode()).decode()

            # SSH commands
            commands = ";".join([
                f"echo '{config_b64}' | base64 -d | sudo tee /etc/dnsmasq.d/wireguard.conf > /dev/null",
                "sudo pkill -HUP dnsmasq 2>/dev/null || (sudo pkill dnsmasq 2>/dev/null; sleep 1; sudo /usr/sbin/dnsmasq -C /etc/dnsmasq.conf)"
            ])

            ssh = paramiko.SSHClient()
            ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            ssh.connect(server['ip'], username=SSH_USER, key_filename=SSH_KEY, timeout=10)

            stdin, stdout, stderr = ssh.exec_command(commands)
            error = stderr.read().decode()
            ssh.close()

            if error and 'error' in error.lower():
                return False, f"SSH error: {error}"

            logger.info(f"dnsmasq WireGuard config deployed to {server_name}")
            return True, f"dnsmasq WireGuard config deployed to {server_name}"

        except Exception as e:
            logger.error(f"Failed to deploy dnsmasq config to {server_name}: {e}")
            return False, str(e)

    def deploy_wg_to_all_servers(self):
        """Deploy WireGuard mesh to all enabled servers.

        Returns:
            dict: {'server_name': {'success': bool, 'wg': str, 'dnsmasq': str}, ...}
        """
        results = {}

        try:
            for server_name in sorted(self.config['servers'].keys()):
                server = self.config['servers'][server_name]
                if not server.get('enabled', True):
                    results[server_name] = {'success': False, 'wg': 'Server disabled', 'dnsmasq': ''}
                    continue

                # Deploy WireGuard config
                wg_success, wg_msg = self.deploy_wg_to_server(server_name)
                dnsmasq_success, dnsmasq_msg = False, ""

                if wg_success:
                    # Deploy dnsmasq binding
                    dnsmasq_success, dnsmasq_msg = self.deploy_wg_dnsmasq_config(server_name)

                results[server_name] = {
                    'success': wg_success and dnsmasq_success,
                    'wg': wg_msg,
                    'dnsmasq': dnsmasq_msg
                }

            logger.info(f"WireGuard deployment complete: {sum(1 for r in results.values() if r['success'])}/{len(results)} servers successful")
            return results

        except Exception as e:
            logger.error(f"Error deploying WireGuard to all servers: {e}")
            return {'error': str(e)}

    def check_wg_status(self, server_ip):
        """Check WireGuard mesh status on a server.

        Args:
            server_ip (str): Server IP address.

        Returns:
            dict: {'wg0_up': bool, 'peers_connected': int, 'interface_ip': str, 'error': str}
        """
        result = {'wg0_up': False, 'peers_connected': 0, 'interface_ip': '', 'error': ''}

        try:
            ssh = paramiko.SSHClient()
            ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            ssh.connect(server_ip, username=SSH_USER, key_filename=SSH_KEY, timeout=5)

            # Check if wg0 exists and get status
            stdin, stdout, stderr = ssh.exec_command("sudo wg show wg0 2>/dev/null")
            wg_output = stdout.read().decode()

            # Get interface IP
            stdin, stdout, stderr = ssh.exec_command("ip addr show wg0 2>/dev/null")
            ip_output = stdout.read().decode()

            ssh.close()

            # Parse results
            result['wg0_up'] = 'interface' in wg_output.lower()

            # Count peers from wg show output
            peer_count = wg_output.count('peer ')
            result['peers_connected'] = peer_count

            # Extract IP from ip addr output
            for line in ip_output.split('\n'):
                if 'inet ' in line and '10.99' in line:
                    parts = line.strip().split()
                    if len(parts) >= 2:
                        result['interface_ip'] = parts[1]
                        break

            if not result['wg0_up']:
                result['error'] = 'WireGuard interface not found or not active'

        except Exception as e:
            result['error'] = str(e)
            logger.error(f"Failed to check WireGuard status on {server_ip}: {e}")

        return result

    def backup_config(self):
        """Export complete configuration as JSON.

        Returns:
            Tuple of (json_string, backup_filename)
        """
        try:
            backup_data = {
                'backup_timestamp': datetime.now().isoformat(),
                'version': '2.0',
                'zones': self.config.get('zones', []),
                'servers': self.config.get('servers', {}),
                'global': self.config.get('global', {})
            }

            filename = f"dnsmasq-ui-backup-{datetime.now().strftime('%Y%m%d-%H%M%S')}.json"
            json_str = json.dumps(backup_data, indent=2)

            return json_str, filename
        except Exception as e:
            logger.error(f"Error creating backup: {str(e)}")
            return None, None

    def restore_config(self, backup_json_str):
        """Restore configuration from JSON backup.

        Args:
            backup_json_str: JSON string containing backup data

        Returns:
            Tuple of (success, message)
        """
        try:
            backup_data = json.loads(backup_json_str)

            # Validate backup structure
            if 'zones' not in backup_data or 'servers' not in backup_data:
                return False, "Invalid backup format: missing zones or servers"

            # Update config with backup data
            self.config['zones'] = backup_data.get('zones', [])
            self.config['servers'] = backup_data.get('servers', {})
            self.config['global'] = backup_data.get('global', {})

            # Save the restored config
            self.save_config()

            logger.info(f"Config restored from backup. {len(self.config['zones'])} zones, {len(self.config['servers'])} servers")
            return True, f"Configuration restored: {len(self.config['zones'])} zones, {len(self.config['servers'])} servers"
        except json.JSONDecodeError:
            return False, "Invalid JSON format in backup file"
        except Exception as e:
            logger.error(f"Error restoring backup: {str(e)}")
            return False, str(e)

    def distribute_key_to_servers(self, public_key_content, password=None):
        """Distribute public key to all servers.

        Args:
            public_key_content: Public key to distribute
            password: Optional password for SSH authentication (for servers without key setup)
        """
        results = {}

        for server_name, server_info in self.get_servers().items():
            if not server_info.get('enabled', True):
                continue

            server_ip = server_info['ip']
            try:
                ssh = paramiko.SSHClient()
                ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())

                # Try key-based auth first, fallback to password if provided
                try:
                    ssh.connect(server_ip, username=SSH_USER, key_filename=SSH_KEY, timeout=5)
                except (paramiko.AuthenticationException, paramiko.SSHException):
                    if password:
                        ssh.connect(server_ip, username=SSH_USER, password=password, timeout=5)
                    else:
                        raise

                # Append public key to authorized_keys
                cmd = f"mkdir -p ~/.ssh && echo '{public_key_content}' >> ~/.ssh/authorized_keys && chmod 600 ~/.ssh/authorized_keys"
                ssh.exec_command(cmd)
                ssh.close()

                results[server_name] = {'success': True, 'message': 'Key distributed'}
            except Exception as e:
                results[server_name] = {'success': False, 'message': str(e)}

        return results

# Initialize manager
manager = ZoneManager(ZONES_FILE)

@app.before_request
def _reload_config_from_disk():
    """Reload zones.json from disk before handling each request.

    ZoneManager otherwise only ever loads it once, at process startup
    (_load_config() in __init__) — and this app runs as three separate
    processes simultaneously (one per DNS server). _sync_peer_state()
    pushes a fresh copy of the file to the *other* two nodes' disks on
    every save, but never tells their already-running processes to pick
    it up, so each process's in-memory config can silently drift from
    what's actually on its own disk.

    Confirmed as the root cause of a real bug: add a host on one node,
    delete it (handled by a different node — e.g. after a keepalived
    failover, or hitting a node directly), then re-add it — the node
    handling the re-add could still have the deleted entry sitting in
    memory and reject the re-add as a duplicate, even though disk
    already correctly had it removed.

    Safe to do unconditionally: app.run() at the bottom of this file
    doesn't pass threaded=True, so Werkzeug serves one request at a
    time — no request can be mid-mutation in this same process when the
    next one reloads."""
    manager.config = manager._load_config()

# Request logging middleware
@app.before_request
def before_request():
    """Log all incoming requests with client IP."""
    client_ip = get_client_ip()
    method = request.method
    path = request.path
    user_agent = request.headers.get('User-Agent', 'unknown')
    logger.info(f"[{client_ip}] {method} {path} | User-Agent: {user_agent}")

# Routes
@app.route('/')
def index():
    """Dashboard with zones and servers overview."""
    zones = manager.get_zones()
    servers_status = {}
    wg_enabled = manager.config.get('global', {}).get('wireguard', {}).get('enabled', False)

    for server_name, server_info in manager.get_servers().items():
        servers_status[server_name] = {
            'ip': server_info['ip'],
            'hostname': server_info['hostname'],
            'status': 'online' if manager.check_server_status(server_info['ip']) else 'offline',
            'tunnel_ip': server_info.get('wireguard', {}).get('tunnel_ip', 'N/A')
        }

        # Add WireGuard status if enabled
        if wg_enabled:
            wg_status = manager.check_wg_status(server_info['ip'])
            servers_status[server_name]['wg'] = {
                'up': wg_status.get('wg0_up', False),
                'interface_ip': wg_status.get('interface_ip', ''),
                'peers_connected': wg_status.get('peers_connected', 0),
                'error': wg_status.get('error', '')
            }

    return render_template('dashboard-v2.html',
                         zones=zones,
                         servers=servers_status,
                         wg_enabled=wg_enabled,
                         total_records=sum(len(z.get('records', [])) for z in zones))

@app.route('/zone/<zone_name>')
def zone_detail(zone_name):
    """Show records for a specific zone."""
    zone = manager.get_zone(zone_name)
    if not zone:
        return redirect(url_for('index'))

    return render_template('zone.html', zone=zone)

@app.route('/api/zones', methods=['GET'])
def api_get_zones():
    """API: Get all zones."""
    return jsonify({'zones': manager.get_zones()})

@app.route('/api/zones', methods=['POST'])
def api_create_zone():
    """API: Create new zone."""
    data = request.json
    success, message = manager.add_zone(
        data.get('name'),
        data.get('description', ''),
        data.get('type', 'local')
    )
    return jsonify({'success': success, 'message': message})

@app.route('/api/zones/<zone_name>', methods=['DELETE'])
def api_delete_zone(zone_name):
    """API: Delete zone."""
    success, message = manager.delete_zone(zone_name)
    return jsonify({'success': success, 'message': message})

@app.route('/api/zones/<zone_name>/records', methods=['GET'])
def api_get_records(zone_name):
    """API: Get records for zone."""
    zone = manager.get_zone(zone_name)
    if not zone:
        return jsonify({'error': 'Zone not found'}), 404

    return jsonify({'zone': zone_name, 'records': zone.get('records', [])})

@app.route('/api/zones/<zone_name>/records', methods=['POST'])
def api_add_record(zone_name):
    """API: Add record to zone."""
    data = request.json
    success, message = manager.add_record(
        zone_name,
        data.get('domain'),
        data.get('type'),
        data.get('value'),
        skip_ptr=bool(data.get('skip_ptr', False))
    )
    return jsonify({'success': success, 'message': message})

@app.route('/api/zones/<zone_name>/records/<path:domain>/<record_type>', methods=['DELETE'])
def api_delete_record(zone_name, domain, record_type):
    """API: Delete record from zone."""
    success, message = manager.delete_record(zone_name, domain, record_type)
    return jsonify({'success': success, 'message': message})

@app.route('/api/acme-hook-keys', methods=['GET'])
def api_list_acme_hook_keys():
    """API: list ACME hook keys (dashboard session required) -- metadata
    only, never the plaintext key or its hash."""
    return jsonify({'keys': manager.get_acme_hook_keys()})

@app.route('/api/acme-hook-keys', methods=['POST'])
def api_create_acme_hook_key():
    """API: generate a new ACME hook key. Returns the plaintext key exactly
    once -- the caller must copy it now, it cannot be retrieved again."""
    data = request.json or {}
    key_id, plaintext = manager.create_acme_hook_key(data.get('label', ''))
    return jsonify({'id': key_id, 'key': plaintext})

@app.route('/api/acme-hook-keys/<key_id>', methods=['DELETE'])
def api_revoke_acme_hook_key(key_id):
    """API: revoke an ACME hook key -- any script still using it starts
    failing on its next call, immediately."""
    removed = manager.revoke_acme_hook_key(key_id)
    if not removed:
        return jsonify({'success': False, 'message': 'Key not found'}), 404
    return jsonify({'success': True, 'message': 'Key revoked'})

@app.route('/api/acme-challenge', methods=['POST'])
@csrf.exempt
def api_add_acme_challenge():
    """API: create an ACME DNS-01 challenge TXT record and publish it
    immediately -- both acme.sh's custom dnsapi hook and certbot's
    manual-auth-hook call this, then ask the CA to validate right away, so
    this blocks until the change is actually live rather than returning
    early. For the 'cloudflare' backend that's the Cloudflare API call
    itself; the 'local' backend additionally pushes to dns31/32/33 and
    restarts dnsmasq on each, same as any other record change.

    CSRF-exempt: CSRF tokens protect session-cookie-authenticated requests
    from being forged by another site in a victim's browser. This endpoint
    is bearer-token authenticated by unattended scripts with no browser or
    session involved -- a CSRF token would just be one more secret the
    hook scripts would need and gain nothing, since forging a cross-site
    request still can't produce a valid Authorization header it doesn't
    have."""
    auth_error = _require_acme_token()
    if auth_error:
        return auth_error

    data = request.json or {}
    fulldomain = data.get('fulldomain', '')
    value = data.get('value', '')
    if not _ACME_CHALLENGE_DOMAIN_RE.match(fulldomain):
        return jsonify({'error': 'fulldomain must be _acme-challenge.<name>'}), 400
    if not _ACME_CHALLENGE_VALUE_RE.match(value):
        return jsonify({'error': 'value is not a well-formed challenge token'}), 400

    success, message = manager.add_txt_challenge(fulldomain, value)
    if not success:
        return jsonify({'success': False, 'message': message}), 404

    # Only actually deploy when this call took the local-zone-file path
    # (see add_txt_challenge's per-domain routing) -- pushing to
    # dns31/32/33 and restarting dnsmasq for a Cloudflare-backed
    # challenge would be a pointless DNS service blip, since nothing
    # local changed. Mirrors the same routing check add_txt_challenge
    # itself just made, not the global ACME_DNS_BACKEND setting, or a
    # per-domain-local challenge would be written to zones.json but
    # never actually pushed to the real servers.
    used_local = manager._zone_for_domain(fulldomain) is not None or ACME_DNS_BACKEND == 'local'
    deploy_results = manager.deploy_to_servers() if used_local else None
    return jsonify({'success': True, 'message': message, 'deploy': deploy_results})

@app.route('/api/acme-challenge', methods=['DELETE'])
@csrf.exempt
def api_remove_acme_challenge():
    """API: remove one ACME challenge TXT value (cleanup hook). See
    api_add_acme_challenge for why this is CSRF-exempt."""
    auth_error = _require_acme_token()
    if auth_error:
        return auth_error

    data = request.json or {}
    fulldomain = data.get('fulldomain', '')
    value = data.get('value', '')
    if not _ACME_CHALLENGE_DOMAIN_RE.match(fulldomain):
        return jsonify({'error': 'fulldomain must be _acme-challenge.<name>'}), 400
    if not _ACME_CHALLENGE_VALUE_RE.match(value):
        return jsonify({'error': 'value is not a well-formed challenge token'}), 400

    success, message = manager.remove_txt_challenge(fulldomain, value)
    if not success:
        return jsonify({'success': False, 'message': message}), 404

    # See api_add_acme_challenge for why this checks the per-domain
    # route, not just the global ACME_DNS_BACKEND setting.
    used_local = manager._zone_for_domain(fulldomain) is not None or ACME_DNS_BACKEND == 'local'
    deploy_results = manager.deploy_to_servers() if used_local else None
    return jsonify({'success': True, 'message': message, 'deploy': deploy_results})

@app.route('/api/deploy', methods=['POST'])
def api_deploy():
    """API: Deploy configuration to all servers."""
    results = manager.deploy_to_servers()
    return jsonify({'results': results})

@app.route('/api/status', methods=['GET'])
def api_status():
    """API: Get status of all servers including keepalived and WireGuard."""
    status = {}
    keepalived_vip = manager.config.get('global', {}).get('keepalive_vip', 'N/A')
    keepalived_vip6 = manager.config.get('global', {}).get('keepalive_vip6')
    wg_enabled = manager.config.get('global', {}).get('wireguard', {}).get('enabled', False)
    subnets = manager.get_subnets()

    for server_name, server_info in manager.get_servers().items():
        dnsmasq_running = manager.check_server_status(server_info['ip'])
        is_master, keepalived_running, ipv6_vip_active = manager.check_keepalived_status(server_info['ip'])
        hostname = server_info.get('hostname', server_name)
        primary_for = [name for name, s in subnets.items() if s.get('primary_dns') == server_name]

        status[server_name] = {
            'ip': server_info['ip'],
            'ipv6': manager.get_server_ipv6(hostname),
            'addresses': manager.get_server_addresses(server_info['ip']),
            'hostname': hostname,
            'primary_for': primary_for,
            'online': dnsmasq_running,
            'dnsmasq': 'active' if dnsmasq_running else 'inactive',
            'keepalived': {
                'running': keepalived_running,
                'status': 'MASTER' if is_master else ('STANDBY' if keepalived_running else 'INACTIVE'),
                'vip': keepalived_vip,
                'vip6': keepalived_vip6,
                'vip6_active': ipv6_vip_active
            },
            'tunnel_ip': server_info.get('wireguard', {}).get('tunnel_ip', 'N/A')
        }

        # Add WireGuard status if enabled
        if wg_enabled:
            wg_status = manager.check_wg_status(server_info['ip'])
            status[server_name]['wireguard'] = wg_status

    return jsonify({
        'servers': status,
        'vip': keepalived_vip,
        'vip6': keepalived_vip6,
        'wg_enabled': wg_enabled
    })

@app.route('/api/dynamic-hosts', methods=['GET'])
def api_dynamic_hosts_list():
    """API: List dynamically-tracked hosts (e.g. DHCPv6 clients kept in sync)."""
    return jsonify({'dynamic_hosts': manager.get_dynamic_hosts()})

@app.route('/api/dynamic-hosts', methods=['POST'])
def api_dynamic_hosts_add():
    """API: Start tracking a specific host's address for automatic DNS updates."""
    data = request.json
    success, message = manager.add_dynamic_host(
        domain=data['domain'],
        zone_name=data['zone'],
        target_host=data.get('target_host'),
        interface=data.get('interface', 'eth0'),
        record_type=data.get('record_type', 'AAAA'),
        ssh_user=data.get('ssh_user'),
        enabled=data.get('enabled', True),
        connection=data.get('connection', 'paramiko'),
        ssh_extra_args=data.get('ssh_extra_args'),
        detect_command=data.get('detect_command'),
        detect_regex=data.get('detect_regex'),
        cli_prompt_regex=data.get('cli_prompt_regex'),
        enable_command=data.get('enable_command'),
        enable_password_ref=data.get('enable_password_ref'),
        logout_command=data.get('logout_command', 'exit'),
        ssh_password_ref=data.get('ssh_password_ref'),
        detect_url=data.get('detect_url'),
        login_url=data.get('login_url'),
        login_fields=data.get('login_fields'),
        login_password_field=data.get('login_password_field'),
        login_password_transform=data.get('login_password_transform', 'none'),
        login_password_ref=data.get('login_password_ref'),
        session_param_regex=data.get('session_param_regex'),
        session_param_name=data.get('session_param_name', 'session_id'),
        verify_tls=data.get('verify_tls', True),
        subnet=data.get('subnet'),
        mac_address=data.get('mac_address'),
        ipv4_host=data.get('ipv4_host'),
        ipv6_host=data.get('ipv6_host'),
        proxmox_update=data.get('proxmox_update')
    )
    return jsonify({'success': success, 'message': message})

@app.route('/api/update-groups', methods=['GET'])
def api_update_groups_list():
    """API: All declared Group Update Plans and their current status."""
    return jsonify({'update_groups': manager.get_update_groups()})

@app.route('/api/update-groups/<group_name>/unlock', methods=['POST'])
def api_update_groups_unlock(group_name):
    """API: Re-verify every member of a locked group's live state and
    clear that group's lock only if all of them check out."""
    success, message, details = manager.verify_and_clear_group_lock(group_name)
    return jsonify({'success': success, 'message': message, 'details': details})

@app.route('/api/update-groups/<group_name>/members', methods=['POST'])
def api_update_groups_add_member(group_name):
    """API: Add a dynamic_hosts entry (by domain) as a member of a group."""
    data = request.json
    success, message = manager.add_group_member(group_name, data.get('domain'))
    return jsonify({'success': success, 'message': message})

@app.route('/api/update-groups/<group_name>/members/<domain>', methods=['DELETE'])
def api_update_groups_remove_member(group_name, domain):
    """API: Remove a member from a group."""
    success, message = manager.remove_group_member(group_name, domain)
    return jsonify({'success': success, 'message': message})

@app.route('/api/update-groups/<group_name>/sync-now', methods=['POST'])
def api_update_groups_sync_now(group_name):
    """API: Manually run a group through its commit-confirm plan right
    now, using each member's currently-expected target instead of
    waiting for a detected subnet prefix change."""
    all_converged, results = manager.sync_update_group(group_name)
    return jsonify({'success': all_converged, 'results': results})

@app.route('/api/proxmox-ipset-tracking', methods=['GET'])
def api_proxmox_ipset_tracking_list():
    """API: Subnets currently tracked into a Proxmox cluster ipset CIDR
    entry, kept in sync automatically on a detected prefix change."""
    return jsonify({'proxmox_ipset_tracking': manager.get_proxmox_ipset_tracking()})

@app.route('/api/proxmox-ipset-tracking', methods=['POST'])
def api_proxmox_ipset_tracking_set():
    """API: Start (or reconfigure) tracking a subnet's live prefix into
    a Proxmox cluster ipset CIDR entry."""
    data = request.json
    success, message = manager.set_proxmox_ipset_tracking(
        data.get('subnet'), data.get('ipset'), data.get('label'))
    return jsonify({'success': success, 'message': message})

@app.route('/api/proxmox-ipset-tracking/<subnet_name>/sync-now', methods=['POST'])
def api_proxmox_ipset_tracking_sync_now(subnet_name):
    """API: Manually (re)sync a tracked subnet's ipset CIDR entry to
    its current live prefix right now, instead of waiting for a
    detected prefix change — creates the entry on first setup, or
    recreates it if a human removed it by hand."""
    success, message = manager.sync_proxmox_ipset_tracking(subnet_name)
    return jsonify({'success': success, 'message': message})

@app.route('/api/subnets', methods=['GET'])
def api_subnets_list():
    """API: List named subnets used for subnet-based address tracking."""
    return jsonify({'subnets': manager.get_subnets()})

@app.route('/api/subnets', methods=['POST'])
def api_subnets_add():
    """API: Register a new named subnet."""
    data = request.json
    success, message = manager.add_subnet(
        name=data['name'],
        cidr_v4=data['cidr_v4'],
        primary_dns=data.get('primary_dns'),
        interface=data.get('interface', 'eth0')
    )
    return jsonify({'success': success, 'message': message})

@app.route('/api/subnets/<name>', methods=['PUT'])
def api_subnets_update(name):
    """API: Update a subnet's CIDR or primary_dns."""
    data = request.json
    success, message = manager.update_subnet(name, **data)
    return jsonify({'success': success, 'message': message})

@app.route('/api/subnets/<name>', methods=['DELETE'])
def api_subnets_delete(name):
    """API: Remove a named subnet (refuses if still referenced)."""
    success, message = manager.delete_subnet(name)
    return jsonify({'success': success, 'message': message})

@app.route('/api/servers/<server_name>/vlans', methods=['GET'])
def api_server_vlans_list(server_name):
    """API: List a server's configured VLAN sub-interfaces."""
    return jsonify({'vlans': manager.get_server_vlans(server_name)})

@app.route('/api/servers/<server_name>/vlans', methods=['POST'])
def api_server_vlans_add(server_name):
    """API: Create and provision a new VLAN sub-interface on a server."""
    data = request.json
    success, message = manager.add_server_vlan(
        server_name,
        vlan_id=data.get('vlan_id'),
        name=data.get('name'),
        ipv4_mode=data.get('ipv4_mode', 'none'),
        ipv4_address=data.get('ipv4_address'),
        ipv6_mode=data.get('ipv6_mode', 'slaac'),
        ipv6_address=data.get('ipv6_address')
    )
    return jsonify({'success': success, 'message': message})

@app.route('/api/servers/<server_name>/vlans/<int:vlan_id>', methods=['PUT'])
def api_server_vlans_update(server_name, vlan_id):
    """API: Update and re-provision an existing VLAN sub-interface."""
    data = request.json
    success, message = manager.update_server_vlan(server_name, vlan_id, **data)
    return jsonify({'success': success, 'message': message})

@app.route('/api/servers/<server_name>/vlans/<int:vlan_id>', methods=['DELETE'])
def api_server_vlans_delete(server_name, vlan_id):
    """API: Tear down a VLAN sub-interface."""
    success, message = manager.delete_server_vlan(server_name, vlan_id)
    return jsonify({'success': success, 'message': message})

@app.route('/api/dynamic-hosts/<domain>', methods=['PUT'])
def api_dynamic_hosts_update(domain):
    """API: Update a tracked host, e.g. enable/disable or change target/interface."""
    data = request.json
    success, message = manager.update_dynamic_host(domain, **data)
    return jsonify({'success': success, 'message': message})

@app.route('/api/dynamic-hosts/<domain>', methods=['DELETE'])
def api_dynamic_hosts_delete(domain):
    """API: Stop tracking a host."""
    success, message = manager.delete_dynamic_host(domain)
    return jsonify({'success': success, 'message': message})

@app.route('/api/dynamic-hosts/poll', methods=['POST'])
def api_dynamic_hosts_poll():
    """API: Immediately poll all tracked hosts and deploy any changes."""
    return jsonify(manager.poll_dynamic_hosts())

@app.route('/api/dynamic-hosts/test', methods=['POST'])
def api_dynamic_hosts_test():
    """API: Dry-run detection against arbitrary settings (not necessarily a
    saved entry) — for iterating on detect_command/detect_regex against a
    device's CLI without committing to zones.json each attempt."""
    return jsonify(manager.test_dynamic_host(request.json))

@app.route('/api/device-credentials/vault', methods=['GET'])
def api_device_credentials_vault_status():
    """API: Whether the credential vault has an admin password set up yet,
    and whether it's currently unlocked in this process's memory."""
    return jsonify({
        'initialized': manager.vault_initialized(),
        'unlocked': manager.vault_unlocked()
    })

@app.route('/api/device-credentials/vault/init', methods=['POST'])
def api_device_credentials_vault_init():
    """API: First-time setup of the credential vault's admin password."""
    data = request.json
    success, message = manager.init_vault(data.get('admin_password', ''))
    return jsonify({'success': success, 'message': message})

@app.route('/api/device-credentials/vault/unlock', methods=['POST'])
def api_device_credentials_vault_unlock():
    """API: Unlock the credential vault for this process (cached in memory
    only — needs repeating after every service restart)."""
    data = request.json
    success, message = manager.unlock_vault(data.get('admin_password', ''))
    return jsonify({'success': success, 'message': message})

@app.route('/api/device-credentials/vault/lock', methods=['POST'])
def api_device_credentials_vault_lock():
    """API: Drop the cached vault key from memory."""
    manager.lock_vault()
    return jsonify({'success': True, 'message': 'Vault locked'})

@app.route('/api/device-credentials', methods=['GET'])
def api_device_credentials_list():
    """API: List device credential keys (e.g. switch enable passwords) —
    never returns the actual stored values."""
    return jsonify({'keys': manager.list_device_credential_keys()})

@app.route('/api/device-credentials/<key>', methods=['PUT'])
def api_device_credentials_set(key):
    """API: Store/update a device credential under `key`, referenced from a
    dynamic_hosts entry's enable_password_ref. Stored in
    DEVICE_CREDENTIALS_FILE (0600, gitignored) — never in zones.json."""
    data = request.json
    success, message = manager.set_device_credential(key, data.get('password', ''))
    return jsonify({'success': success, 'message': message})

@app.route('/api/device-credentials/<key>', methods=['DELETE'])
def api_device_credentials_delete(key):
    """API: Remove a stored device credential."""
    success, message = manager.delete_device_credential(key)
    return jsonify({'success': success, 'message': message})

@app.route('/api/2fa/status', methods=['GET'])
def api_2fa_status():
    """API: Which 2FA methods are enabled, and the email address in use
    (if any) — never returns the TOTP secret or any code."""
    auth_config = _load_auth() or {}
    tf = auth_config.get('two_factor', {})
    return jsonify({
        'totp_enabled': bool(tf.get('totp', {}).get('enabled')),
        'email_enabled': bool(tf.get('email', {}).get('enabled')),
        'email_to': tf.get('email', {}).get('to') if tf.get('email', {}).get('enabled') else None
    })

@app.route('/api/2fa/totp/setup', methods=['POST'])
def api_2fa_totp_setup():
    """API: Generate a new (not yet enabled) TOTP secret for the admin to
    add to their authenticator app. Stashed in the session until confirmed."""
    auth_secret = pyotp.random_base32()
    session['pending_totp_secret'] = auth_secret
    uri = pyotp.TOTP(auth_secret).provisioning_uri(name='admin', issuer_name='dnsmasq-ui')
    return jsonify({'secret': auth_secret, 'provisioning_uri': uri})

@app.route('/api/2fa/totp/confirm', methods=['POST'])
def api_2fa_totp_confirm():
    """API: Enable TOTP once the admin proves they can generate a valid
    code from the secret issued by /setup."""
    pending_secret = session.get('pending_totp_secret')
    if not pending_secret:
        return jsonify({'success': False, 'message': 'No pending TOTP setup — call setup first'}), 400

    code = (request.json or {}).get('code', '').strip()
    if not pyotp.TOTP(pending_secret).verify(code, valid_window=1):
        return jsonify({'success': False, 'message': 'Invalid code'}), 400

    auth_config = _load_auth() or {}
    auth_config.setdefault('two_factor', {})['totp'] = {'enabled': True, 'secret': pending_secret}
    _save_auth(auth_config)
    session.pop('pending_totp_secret', None)
    return jsonify({'success': True, 'message': 'TOTP enabled'})

@app.route('/api/2fa/totp/disable', methods=['POST'])
def api_2fa_totp_disable():
    """API: Disable TOTP. Requires the current password again, since this
    removes a layer of protection from the account."""
    auth_config = _load_auth() or {}
    password = (request.json or {}).get('current_password', '')
    if not check_password_hash(auth_config.get('password_hash', ''), password):
        return jsonify({'success': False, 'message': 'Incorrect password'}), 403
    auth_config.setdefault('two_factor', {})['totp'] = {'enabled': False}
    _save_auth(auth_config)
    return jsonify({'success': True, 'message': 'TOTP disabled'})

@app.route('/api/2fa/email/setup', methods=['POST'])
def api_2fa_email_setup():
    """API: Send a test code to the given address; enabling happens on
    /confirm once the admin proves they received it."""
    to_addr = (request.json or {}).get('to', '').strip()
    if not to_addr:
        return jsonify({'success': False, 'message': 'Email address required'}), 400

    code = f"{secrets.randbelow(1000000):06d}"
    session['pending_email_to'] = to_addr
    session['pending_email_code'] = code
    session['pending_email_code_expires'] = (datetime.now() + timedelta(minutes=10)).isoformat()
    success, message = _send_email_code(to_addr, code)
    return jsonify({'success': success, 'message': message})

@app.route('/api/2fa/email/confirm', methods=['POST'])
def api_2fa_email_confirm():
    """API: Enable email 2FA once the admin proves they received the test code."""
    pending_to = session.get('pending_email_to')
    pending_code = session.get('pending_email_code')
    pending_expires = session.get('pending_email_code_expires')
    if not pending_to or not pending_code:
        return jsonify({'success': False, 'message': 'No pending email setup — call setup first'}), 400
    if not pending_expires or datetime.now() > datetime.fromisoformat(pending_expires):
        return jsonify({'success': False, 'message': 'Code expired — request a new one'}), 400

    code = (request.json or {}).get('code', '').strip()
    if code != pending_code:
        return jsonify({'success': False, 'message': 'Invalid code'}), 400

    auth_config = _load_auth() or {}
    auth_config.setdefault('two_factor', {})['email'] = {'enabled': True, 'to': pending_to}
    _save_auth(auth_config)
    for k in ('pending_email_to', 'pending_email_code', 'pending_email_code_expires'):
        session.pop(k, None)
    return jsonify({'success': True, 'message': 'Email 2FA enabled'})

@app.route('/api/2fa/email/disable', methods=['POST'])
def api_2fa_email_disable():
    """API: Disable email 2FA. Requires the current password again."""
    auth_config = _load_auth() or {}
    password = (request.json or {}).get('current_password', '')
    if not check_password_hash(auth_config.get('password_hash', ''), password):
        return jsonify({'success': False, 'message': 'Incorrect password'}), 403
    auth_config.setdefault('two_factor', {})['email'] = {'enabled': False}
    _save_auth(auth_config)
    return jsonify({'success': True, 'message': 'Email 2FA disabled'})

@app.route('/config')
def config_page():
    """Configuration page for SSH keys and server management."""
    return render_template('config.html')

@app.route('/api/config/ssh', methods=['GET'])
def api_get_ssh_config():
    """API: Get SSH key information."""
    key_info = manager.get_ssh_key_info()
    return jsonify(key_info)

@app.route('/api/config/ssh/generate', methods=['POST'])
def api_generate_ssh_key():
    """API: Generate new SSH key pair."""
    result = manager.generate_ssh_key()
    return jsonify(result)

@app.route('/api/config/ssh/upload', methods=['POST'])
def api_upload_ssh_key():
    """API: Upload and set new SSH key."""
    try:
        if 'private_key' not in request.files:
            return jsonify({'success': False, 'message': 'No private key file provided'}), 400

        private_key_file = request.files['private_key']
        if not private_key_file.filename:
            return jsonify({'success': False, 'message': 'Empty file'}), 400

        # Read the uploaded key
        key_content = private_key_file.read().decode()

        # Save it to the configured location
        key_path = SSH_KEY
        os.makedirs(os.path.dirname(key_path), exist_ok=True)
        with open(key_path, 'w') as f:
            f.write(key_content)
        os.chmod(key_path, 0o600)

        logger.info(f"New SSH key uploaded to {key_path}")
        return jsonify({'success': True, 'message': 'SSH key uploaded successfully'})
    except Exception as e:
        logger.error(f"Error uploading SSH key: {str(e)}")
        return jsonify({'success': False, 'message': str(e)}), 500

@app.route('/api/config/backup', methods=['GET'])
def api_backup_config():
    """API: Download configuration backup as JSON file."""
    try:
        json_str, filename = manager.backup_config()
        if not json_str:
            return jsonify({'error': 'Failed to create backup'}), 500

        # Return JSON as downloadable file
        from io import BytesIO
        return send_file(
            BytesIO(json_str.encode()),
            mimetype='application/json',
            as_attachment=True,
            download_name=filename
        )
    except Exception as e:
        logger.error(f"Error downloading backup: {str(e)}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/config/restore', methods=['POST'])
def api_restore_config():
    """API: Restore configuration from JSON backup file."""
    try:
        if 'backup_file' not in request.files:
            return jsonify({'success': False, 'message': 'No backup file provided'}), 400

        backup_file = request.files['backup_file']
        if not backup_file.filename:
            return jsonify({'success': False, 'message': 'Empty file'}), 400

        # Read backup content
        backup_content = backup_file.read().decode()

        # Restore config
        success, message = manager.restore_config(backup_content)

        if success:
            return jsonify({'success': True, 'message': message})
        else:
            return jsonify({'success': False, 'message': message}), 400
    except Exception as e:
        logger.error(f"Error restoring config: {str(e)}")
        return jsonify({'success': False, 'message': str(e)}), 500

@app.route('/api/config/restore-and-deploy', methods=['POST'])
def api_restore_and_deploy():
    """API: Restore configuration and automatically deploy to all servers."""
    try:
        if 'backup_file' not in request.files:
            return jsonify({'success': False, 'message': 'No backup file provided'}), 400

        backup_file = request.files['backup_file']
        if not backup_file.filename:
            return jsonify({'success': False, 'message': 'Empty file'}), 400

        # Read backup content
        backup_content = backup_file.read().decode()

        # Restore config
        success, message = manager.restore_config(backup_content)

        if not success:
            return jsonify({'success': False, 'message': message}), 400

        # Deploy to all servers
        deploy_results = manager.deploy_to_servers()

        return jsonify({
            'success': True,
            'message': message,
            'deploy_results': deploy_results
        })
    except Exception as e:
        logger.error(f"Error in restore and deploy: {str(e)}")
        return jsonify({'success': False, 'message': str(e)}), 500

@app.route('/api/config/ssh/servers', methods=['GET'])
def api_get_servers_for_sync():
    """API: Get list of servers for key sync."""
    servers = []
    for server_name, server_info in manager.get_servers().items():
        if server_info.get('enabled', True):
            servers.append({
                'name': server_name,
                'ip': server_info['ip'],
                'hostname': server_info.get('hostname', server_name)
            })
    return jsonify({'servers': servers})

@app.route('/api/config/ssh/sync', methods=['POST'])
def api_sync_ssh_key():
    """API: Distribute public key to all servers.

    Supports both key-based and password-based authentication.
    """
    try:
        data = request.json
        public_key = data.get('public_key', '')
        password = data.get('password', None)

        if not public_key:
            return jsonify({'success': False, 'message': 'No public key provided'}), 400

        results = manager.distribute_key_to_servers(public_key, password=password)
        return jsonify({'success': True, 'results': results})
    except Exception as e:
        logger.error(f"Error syncing SSH key: {str(e)}")
        return jsonify({'success': False, 'message': str(e)}), 500

# WireGuard Mesh API Endpoints

@app.route('/api/wireguard/generate-keys', methods=['POST'])
def api_wireguard_generate_keys():
    """API: Generate WireGuard keypairs for all servers."""
    try:
        overwrite = request.json.get('overwrite', False) if request.json else False
        result = manager.generate_wg_keys_for_all_servers(overwrite=overwrite)
        return jsonify(result)
    except Exception as e:
        logger.error(f"Error generating WireGuard keys: {str(e)}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/wireguard/validate', methods=['GET'])
def api_wireguard_validate():
    """API: Validate WireGuard configuration completeness."""
    try:
        result = manager.validate_wg_config()
        return jsonify(result)
    except Exception as e:
        logger.error(f"Error validating WireGuard config: {str(e)}")
        return jsonify({'valid': False, 'errors': [str(e)]}), 500

@app.route('/api/wireguard/config/<server_name>', methods=['GET'])
def api_wireguard_config(server_name):
    """API: Get WireGuard wg0.conf preview for a server."""
    try:
        success, config = manager.generate_wg_config_for_server(server_name)
        if success:
            return jsonify({'success': True, 'server': server_name, 'config': config})
        else:
            return jsonify({'success': False, 'error': config}), 400
    except Exception as e:
        logger.error(f"Error generating config for {server_name}: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/wireguard/deploy', methods=['POST'])
def api_wireguard_deploy_all():
    """API: Deploy WireGuard mesh to all enabled servers."""
    try:
        results = manager.deploy_wg_to_all_servers()
        return jsonify(results)
    except Exception as e:
        logger.error(f"Error deploying WireGuard: {str(e)}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/wireguard/deploy/<server_name>', methods=['POST'])
def api_wireguard_deploy_single(server_name):
    """API: Deploy WireGuard to a single server."""
    try:
        wg_success, wg_msg = manager.deploy_wg_to_server(server_name)
        dnsmasq_success, dnsmasq_msg = False, ""

        if wg_success:
            dnsmasq_success, dnsmasq_msg = manager.deploy_wg_dnsmasq_config(server_name)

        return jsonify({
            'success': wg_success and dnsmasq_success,
            'server': server_name,
            'wg': {'success': wg_success, 'message': wg_msg},
            'dnsmasq': {'success': dnsmasq_success, 'message': dnsmasq_msg}
        })
    except Exception as e:
        logger.error(f"Error deploying WireGuard to {server_name}: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/wireguard/status', methods=['GET'])
def api_wireguard_status():
    """API: Check WireGuard mesh status on all servers."""
    try:
        results = {}
        for server_name, server_info in manager.get_servers().items():
            if server_info.get('enabled', True):
                results[server_name] = manager.check_wg_status(server_info['ip'])
        return jsonify(results)
    except Exception as e:
        logger.error(f"Error checking WireGuard status: {str(e)}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/proxy-check', methods=['GET'])
def api_proxy_check():
    """Public diagnostic (no login required — reveals only request
    metadata the client already knows about itself): shows how this
    request was actually received, to verify a reverse proxy in front of
    this app (e.g. Pangolin) is passing X-Forwarded-* headers correctly
    and that ProxyFix (configured with x_for=1, x_proto=1, x_host=1,
    x_port=1 — see near the top of this file) is trusting them.

    'direct_connection' is the connection Werkzeug actually saw before
    ProxyFix rewrote it — i.e. the reverse proxy's own IP/scheme/host.
    'resolved' is what ProxyFix derived after trusting one hop of
    X-Forwarded-*. If both match, either there's no proxy in front of
    this request, or one's there but isn't setting the headers.

    'expected_origin' is exactly what it sounds like to Flask-WTF's CSRF
    protection too — CSRFProtect checks an HTTPS request's Origin/Referer
    against request.host_url, which is derived from the same
    scheme/host ProxyFix resolves above. If 'resolved' correctly shows
    https/the public hostname, CSRF checks behind the proxy work
    correctly for free; if ProxyFix were misconfigured (or x_proto wasn't
    trusted) this would still show http, and every state-changing request
    through the proxy would fail CSRF validation.
    """
    orig = request.environ.get('werkzeug.proxy_fix.orig', {})
    orig_addr = orig.get('REMOTE_ADDR')
    orig_scheme = orig.get('wsgi.url_scheme')
    orig_host = orig.get('HTTP_HOST')

    behind_proxy = orig_addr is not None and orig_addr != request.remote_addr

    return jsonify({
        'resolved': {
            'client_ip': request.remote_addr,
            'scheme': request.scheme,
            'host': request.host,
            'is_secure': request.is_secure
        },
        'direct_connection': {
            'ip': orig_addr,
            'scheme': orig_scheme,
            'host': orig_host
        } if orig_addr is not None else None,
        'expected_origin': request.host_url.rstrip('/'),
        'behind_proxy': behind_proxy,
        'verdict': (
            "Reachable through a reverse proxy, and X-Forwarded-* headers are being trusted correctly "
            "-- CSRF's expected origin above should match your public URL."
            if behind_proxy else
            "No difference between the direct connection and the resolved client info — either this "
            "request came in directly (not through a proxy), or a proxy in front of it isn't setting "
            "X-Forwarded-For/Proto/Host. If you expected this to be through a proxy, CSRF-protected "
            "requests (login, saving config, etc.) will likely fail until that's fixed, since "
            "expected_origin above won't match what your browser actually sends."
        )
    })

@app.errorhandler(404)
def not_found(error):
    return jsonify({'error': 'Not found'}), 404

@app.errorhandler(500)
def server_error(error):
    return jsonify({'error': 'Internal server error'}), 500

DYNAMIC_POLL_INTERVAL = int(os.getenv('DYNAMIC_POLL_INTERVAL', '300'))

def _dynamic_host_poller():
    """Background loop: periodically sync tracked hosts' DNS records to their
    current address (e.g. after a DHCPv6 lease renewal).

    In an HA deployment (dnsmasq-ui running on multiple DNS servers), only
    the node currently holding the keepalived VIP actually polls — every
    instance running this independently would mean redundant, simultaneous
    login attempts against the same switches/routers from multiple sources,
    which is exactly the kind of thing that made devices flaky earlier in
    this project's dynamic_hosts work. Single-instance deployments still
    work fine: if this host isn't part of a keepalived setup at all, the VIP
    just never matches and this silently no-ops rather than erroring.
    """
    while True:
        time.sleep(DYNAMIC_POLL_INTERVAL)
        try:
            vip = manager.config.get('global', {}).get('keepalive_vip', '192.168.0.230')
            if not _is_local_vrrp_master(vip):
                logger.info("Not the current keepalived master — skipping dynamic host poll")
                continue
            result = manager.poll_dynamic_hosts()
            if result['deployed']:
                logger.info(f"Dynamic host poll applied changes: {result['changes']}")

            drifted, configured, actual = manager.check_ipv6_vip_drift()
            if drifted:
                logger.error(f"IPv6 VIP drift: configured {configured}, active node is actually on {actual}")
                if not manager._v6_vip_drift_notified:
                    manager._notify_ipv6_vip_drift(configured, actual)
            else:
                manager._v6_vip_drift_notified = False  # a future drift should send a fresh notice
        except Exception as e:
            logger.error(f"Dynamic host poll failed: {e}")

if __name__ == '__main__':
    threading.Thread(target=_dynamic_host_poller, daemon=True).start()
    # '::' rather than '0.0.0.0' — with the OS default of
    # net.ipv6.bindv6only=0, a single dual-stack socket serves both IPv4 and
    # IPv6 clients (needed for the dashboard to be reachable on the IPv6
    # keepalived VIP as well as the IPv4 one).
    app.run(host='::', port=5000, debug=False)
