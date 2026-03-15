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
from cryptography.hazmat.backends import default_backend
import hashlib
import base64

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
                    'keepalive_vip': '192.168.0.250',
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
            stdin, stdout, stderr = ssh.exec_command("sudo systemctl is-active dnsmasq")
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

            # Check if keepalived is running
            stdin, stdout, stderr = ssh.exec_command("sudo systemctl is-active keepalived 2>/dev/null")
            keepalived_status = stdout.read().decode().strip()
            keepalived_running = 'active' in keepalived_status.lower()

            if not keepalived_running:
                ssh.close()
                return False, False

            # Check if this is the master (MASTER state in keepalived)
            stdin, stdout, stderr = ssh.exec_command("sudo systemctl status keepalived 2>/dev/null | grep -i master")
            output = stdout.read().decode().strip()
            ssh.close()

            is_master = len(output) > 0 and 'MASTER' in output.upper()
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

@app.errorhandler(404)
def not_found(error):
    return jsonify({'error': 'Not found'}), 404

@app.errorhandler(500)
def server_error(error):
    return jsonify({'error': 'Internal server error'}), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=False)
