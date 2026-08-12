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
SSH_KEY = os.getenv('SSH_KEY', os.path.expanduser('~/.ssh/id_rsa'))
SSH_USER = os.getenv('SSH_USER', 'debian')
# Resolved as an absolute path rather than relying on PATH — the systemd
# unit sets PATH to just the venv's bin dir, which hides /usr/bin/ssh from
# subprocess-based lookups.
SSH_BIN = next((p for p in ('/usr/bin/ssh', '/bin/ssh', '/usr/local/bin/ssh') if os.path.exists(p)), 'ssh')
DOCKER_BIN = next((p for p in ('/usr/bin/docker', '/usr/local/bin/docker') if os.path.exists(p)), 'docker')
SUDO_BIN = next((p for p in ('/usr/bin/sudo', '/bin/sudo') if os.path.exists(p)), 'sudo')
IP_BIN = next((p for p in ('/usr/sbin/ip', '/sbin/ip', '/usr/bin/ip') if os.path.exists(p)), 'ip')
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
    VIP against itself and silently never detect drift."""
    try:
        result = subprocess.run([IP_BIN, '-6', '-o', 'addr', 'show', 'scope', 'global', 'dynamic'],
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
    '/api/proxy-check'
}

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

class ZoneManager:
    """Manages DNS zones and records."""

    def __init__(self, zones_file):
        self.zones_file = zones_file
        self.config = self._load_config()
        self._vault_key = None  # in-memory only; never persisted to disk
        self._vault_lock_notified = False  # avoid re-emailing every poll cycle for the same lock
        self._v6_vip_drift_notified = False  # avoid re-emailing every poll cycle for the same drift

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
        """Push zones.json/auth.json/device-credentials.json/smtp.env to the
        other dnsmasq-ui instances (the other DNS servers, in an HA
        deployment) so a keepalived failover doesn't hand off to a peer with
        stale config, a different session-signing secret, or a differently
        keyed vault. Best-effort and synchronous — a briefly-unreachable
        peer logs an error but doesn't block the save that triggered this."""
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

    def add_record(self, zone_name, domain, record_type, value):
        """Add record to zone."""
        zone = self.get_zone(zone_name)
        if not zone:
            return False, "Zone not found"

        record = {
            'domain': domain,
            'type': record_type,
            'value': value
        }
        zone['records'].append(record)
        self.save_config()
        return True, "Record added"

    def update_record(self, zone_name, domain, record_type, new_value):
        """Update record in zone."""
        zone = self.get_zone(zone_name)
        if not zone:
            return False, "Zone not found"

        for record in zone['records']:
            if record['domain'] == domain and record['type'] == record_type:
                record['value'] = new_value
                self.save_config()
                return True, "Record updated"

        return False, "Record not found"

    def delete_record(self, zone_name, domain, record_type):
        """Delete record from zone."""
        zone = self.get_zone(zone_name)
        if not zone:
            return False, "Zone not found"

        zone['records'] = [r for r in zone['records']
                          if not (r['domain'] == domain and r['type'] == record_type)]
        self.save_config()
        return True, "Record deleted"

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
                    config += f"cname={domain},{value}\n"
                else:
                    config += f"address=/{domain}/{value}\n"
            config += "\n"

        # Add upstream DNS
        upstream = self.config.get('global', {}).get('upstream_dns', ['1.1.1.1', '8.8.8.8'])
        config += "# Upstream DNS\n"
        for dns in upstream:
            config += f"server={dns}\n"

        return config

    def deploy_to_servers(self):
        """Deploy configuration to all enabled servers."""
        servers = self.config.get('servers', {})
        dnsmasq_config = self.generate_dnsmasq_config()
        results = {}

        for server_name, server_info in servers.items():
            if not server_info.get('enabled', True):
                continue

            success, message = self._ssh_update(
                server_info['ip'],
                dnsmasq_config
            )
            results[server_name] = {'success': success, 'message': message}

        return results

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

    def check_server_status(self, server_ip):
        """Check if dnsmasq is running on server."""
        try:
            ssh = paramiko.SSHClient()
            ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            ssh.connect(server_ip, username=SSH_USER, key_filename=SSH_KEY, timeout=5)
            # Use pgrep instead of systemctl for Docker/non-systemd containers
            stdin, stdout, stderr = ssh.exec_command("pgrep -x dnsmasq > /dev/null && echo active || echo inactive")
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

    def add_subnet(self, name, cidr_v4, primary_dns=None):
        """Register a new named subnet for subnet-based address tracking.
        primary_dns is a server name from `servers` (not a raw IP) --
        picking a DNS server this app already manages means one less
        thing for the user to keep in sync by hand if that server's IP
        ever changes."""
        subnets = self.get_subnets()
        if name in subnets:
            return False, "Subnet already exists"
        if primary_dns and primary_dns not in self.get_servers():
            return False, f"'{primary_dns}' is not a known server"
        try:
            ipaddress.IPv4Network(cidr_v4, strict=False)
        except ValueError as e:
            return False, f"Invalid CIDR: {e}"
        subnets[name] = {'cidr_v4': cidr_v4, 'prefix_v6': None, 'primary_dns': primary_dns}
        self.save_config()
        return True, "Subnet added"

    def update_subnet(self, name, **fields):
        """Update a subnet's cidr_v4/primary_dns. prefix_v6 is
        intentionally not settable here -- it's only ever written by
        poll_subnets() detecting the live prefix from primary_dns."""
        allowed = ('cidr_v4', 'primary_dns')
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

    def add_dynamic_host(self, domain, zone_name, target_host=None, interface='eth0',
                          record_type='AAAA', ssh_user=None, enabled=True,
                          connection='paramiko', ssh_extra_args=None,
                          detect_command=None, detect_regex=None,
                          cli_prompt_regex=None, enable_command=None,
                          enable_password_ref=None, logout_command='exit',
                          ssh_password_ref=None, detect_url=None, login_url=None,
                          subnet=None, mac_address=None, ipv4_host=None,
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

        subnet/mac_address/ipv4_host: the cheaper alternative to all of the
        above — for a device on a subnet with a primary_dns configured
        (see add_subnet), its address is computed from that subnet's live
        prefix instead of polling the device directly. mac_address is used
        for AAAA (SLAAC/EUI-64 derives the suffix from it — see the
        subnet-tracking section in README.md), ipv4_host is an explicit
        host number for A. Mutually exclusive with target_host/connection/
        detect_command/etc — this bypasses per-device polling entirely.
        """
        if not self.get_zone(zone_name):
            return False, "Zone not found"

        for entry in self.config['dynamic_hosts']:
            if entry['domain'] == domain and entry['record_type'] == record_type:
                return False, "Already tracked"

        if subnet:
            if subnet not in self.get_subnets():
                return False, f"Unknown subnet '{subnet}'"
            if record_type == 'AAAA' and not mac_address:
                return False, "mac_address is required for AAAA subnet tracking"
            if record_type == 'A' and ipv4_host is None:
                return False, "ipv4_host is required for A subnet tracking"
            self.config['dynamic_hosts'].append({
                'domain': domain,
                'zone': zone_name,
                'record_type': record_type,
                'subnet': subnet,
                'mac_address': mac_address,
                'ipv4_host': ipv4_host,
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
                   'verify_tls', 'subnet', 'mac_address', 'ipv4_host')
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
        self.save_config()
        return before != len(self.config['dynamic_hosts']), "Dynamic host removed"

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
            configured_net = ipaddress.ip_interface(configured).network
        except ValueError:
            configured_net = ipaddress.ip_network(configured, strict=False)
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
            output = self._run_ssh_paramiko(ip, 'ip -6 -o addr show eth0 scope global dynamic', None)
            if not output:
                logger.error(f"Subnet '{name}': failed to detect IPv6 prefix via {ip}")
                continue
            match = re.search(r'inet6 ([0-9a-fA-F:]+)/', output)
            if not match:
                logger.error(f"Subnet '{name}': no dynamic global IPv6 address found on {ip}")
                continue
            try:
                subnet['prefix_v6'] = str(ipaddress.ip_interface(f"{match.group(1)}/64").network)
            except ValueError:
                logger.error(f"Subnet '{name}': '{match.group(1)}' is not a valid address")

    def _detect_subnet_member_address(self, entry):
        """Compute a subnet-tracked device's current address from its
        subnet's live prefix/CIDR plus its own MAC (AAAA) or host number
        (A) — no per-device polling needed, see poll_subnets()."""
        subnet = self.config.get('global', {}).get('subnets', {}).get(entry.get('subnet'))
        if not subnet:
            logger.error(f"'{entry['domain']}' references unknown subnet '{entry.get('subnet')}'")
            return None
        record_type = entry.get('record_type', 'AAAA')
        try:
            if record_type == 'AAAA':
                prefix_v6, mac = subnet.get('prefix_v6'), entry.get('mac_address')
                if not prefix_v6 or not mac:
                    return None
                return str(_ipv6_from_prefix_and_mac(ipaddress.ip_network(prefix_v6), mac))
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
        data.get('value')
    )
    return jsonify({'success': success, 'message': message})

@app.route('/api/zones/<zone_name>/records/<path:domain>/<record_type>', methods=['DELETE'])
def api_delete_record(zone_name, domain, record_type):
    """API: Delete record from zone."""
    success, message = manager.delete_record(zone_name, domain, record_type)
    return jsonify({'success': success, 'message': message})

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
        ipv4_host=data.get('ipv4_host')
    )
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
        primary_dns=data.get('primary_dns')
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
