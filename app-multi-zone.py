#!/usr/bin/env python3
"""
dnsmasq-ui v2: Enhanced web UI with multi-zone support.
Manages dnsmasq DNS records across multiple servers and zones.
"""

from flask import Flask, render_template, request, jsonify, redirect, url_for, send_file
from flask_cors import CORS
from werkzeug.middleware.proxy_fix import ProxyFix
import json
import os
import subprocess
from pathlib import Path
from datetime import datetime
import paramiko
import logging
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
from cryptography.hazmat.backends import default_backend
import hashlib
import base64
import ipaddress

app = Flask(__name__)
CORS(app)

# Reverse proxy support: trust X-Forwarded-For, X-Forwarded-Proto, X-Forwarded-Host
# Handles proper IP tracking and URL construction behind reverse proxies
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_port=1)

# Logging for request tracking
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Configuration
ZONES_FILE = os.getenv('ZONES_CONFIG', 'zones.json')
DNSMASQ_RECORDS_FILE = os.getenv('DNSMASQ_RECORDS_FILE', '/etc/dnsmasq.d/local-records.conf')
SSH_KEY = os.getenv('SSH_KEY', os.path.expanduser('~/.ssh/id_rsa'))
SSH_USER = os.getenv('SSH_USER', 'debian')
WG_KEYS_FILE = os.getenv(
    'WG_KEYS_FILE',
    os.path.join(os.path.dirname(os.path.abspath(ZONES_FILE)), 'wireguard-keys.json')
)

# Reverse proxy configuration
PROXY_PATH_PREFIX = os.getenv('PROXY_PATH_PREFIX', '')  # e.g., '/dnsmasq-ui' for http://proxy/dnsmasq-ui/
TRUSTED_PROXIES = os.getenv('TRUSTED_PROXIES', '*').split(',')  # Comma-separated IPs or '*'

def get_client_ip():
    """Get client IP, respecting X-Forwarded-For from reverse proxy."""
    return request.remote_addr

def log_request():
    """Log incoming request with client IP and path."""
    client_ip = get_client_ip()
    method = request.method
    path = request.path
    logger.info(f"{client_ip} {method} {path}")

class ZoneManager:
    """Manages DNS zones and records."""

    def __init__(self, zones_file):
        self.zones_file = zones_file
        self.config = self._load_config()

    def _load_config(self):
        """Load zones and servers configuration."""
        try:
            with open(self.zones_file) as f:
                return json.load(f)
        except:
            return {
                'zones': [],
                'servers': {},
                'global': {
                    'upstream_dns': ['1.1.1.1', '8.8.8.8'],
                    'keepalive_vip': '192.168.0.252',
                    'keepalive_interval': 300
                }
            }

    def save_config(self):
        """Save zones configuration to file."""
        with open(self.zones_file, 'w') as f:
            json.dump(self.config, f, indent=2)

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

            # Write config and restart
            cmd = f"echo '{config_content}' | sudo tee {DNSMASQ_RECORDS_FILE} > /dev/null && sudo systemctl restart dnsmasq"
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
            Tuple of (is_master, keepalived_running)
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
                return False, False

            # Check if this is the master by checking VIP assignment
            stdin, stdout, stderr = ssh.exec_command("ip addr show | grep -q 172.20.0.252 && echo MASTER || echo BACKUP")
            output = stdout.read().decode().strip()
            ssh.close()

            is_master = 'MASTER' in output.upper()
            return is_master, keepalived_running
        except:
            return False, False

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
                'bits': pkey.get_bits()
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
                "sudo apt-get update -qq 2>/dev/null",
                "sudo apt-get install -y wireguard-tools 2>/dev/null",
                "sudo mkdir -p /etc/wireguard && sudo chmod 0700 /etc/wireguard",
                f"echo '{config_b64}' | base64 -d | sudo tee /etc/wireguard/wg0.conf > /dev/null",
                "sudo chmod 0600 /etc/wireguard/wg0.conf",
                "sudo systemctl enable wg-quick@wg0 2>/dev/null",
                "sudo systemctl restart wg-quick@wg0"
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
                "sudo systemctl restart dnsmasq"
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

    for server_name, server_info in manager.get_servers().items():
        servers_status[server_name] = {
            'ip': server_info['ip'],
            'hostname': server_info['hostname'],
            'status': 'online' if manager.check_server_status(server_info['ip']) else 'offline'
        }

    return render_template('dashboard-v2.html',
                         zones=zones,
                         servers=servers_status,
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
    """API: Get status of all servers including keepalived."""
    status = {}
    keepalived_vip = manager.config.get('global', {}).get('keepalive_vip', 'N/A')

    for server_name, server_info in manager.get_servers().items():
        dnsmasq_running = manager.check_server_status(server_info['ip'])
        is_master, keepalived_running = manager.check_keepalived_status(server_info['ip'])

        status[server_name] = {
            'ip': server_info['ip'],
            'hostname': server_info.get('hostname', server_name),
            'online': dnsmasq_running,
            'dnsmasq': 'active' if dnsmasq_running else 'inactive',
            'keepalived': {
                'running': keepalived_running,
                'status': 'MASTER' if is_master else ('STANDBY' if keepalived_running else 'INACTIVE'),
                'vip': keepalived_vip
            }
        }
    return jsonify({
        'servers': status,
        'vip': keepalived_vip
    })

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

@app.errorhandler(404)
def not_found(error):
    return jsonify({'error': 'Not found'}), 404

@app.errorhandler(500)
def server_error(error):
    return jsonify({'error': 'Internal server error'}), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=False)
