# Secrets Management Guide

This document explains how to securely manage credentials and sensitive data for dnsmasq-ui infrastructure deployments.

## Overview

Sensitive information (passwords, API keys, SSH keys) should never be committed to version control. This project uses multiple layers to protect secrets:

1. **Environment Variables (.env)** - Local machine configuration
2. **Ansible Vault** - Encrypted Ansible variables
3. **.gitignore** - Prevents accidental commits
4. **SSH Key-Based Authentication** - Preferred over passwords

## Quick Start

### 1. Create Local Configuration File

```bash
# Copy the environment template
cp .env.example .env

# Edit with your actual values
nano .env

# Source it before running commands
source .env
```

### 2. Create Ansible Vault for Secrets

```bash
# Copy the vault template
cp ansible/.vault-example ansible/vault.yml

# Edit with your secrets
nano ansible/vault.yml

# Encrypt the vault file
ansible-vault encrypt ansible/vault.yml

# Ansible will prompt for vault password on each run, or use:
ansible-vault view ansible/vault.yml
```

### 3. Add to .gitignore

```bash
# Add secrets patterns to your .gitignore
cat .gitignore-secrets >> .gitignore

# Verify nothing secret is staged
git status

# Remove any accidentally staged secrets
git rm --cached .env ansible/vault.yml
git commit -m "Remove accidentally committed secrets"
```

## Usage Patterns

### Environment Variables in Ansible

#### Load from .env file:

```bash
# Export all variables from .env
export $(cat .env | grep -v '^#' | xargs)

# Run playbook with environment variables
ansible-playbook ansible/proxmox-management.yml \
  -e "proxmox_password=${PROXMOX_PASSWORD}" \
  -e "vm_id=${VM_ID}"
```

#### Or use a vars file:

```bash
ansible-playbook ansible/proxmox-management.yml \
  -e "@.env.vars"  # If you convert .env to YAML
```

### Ansible Vault Usage

#### Run playbooks with vault:

```bash
# Prompts for vault password interactively
ansible-playbook ansible/proxmox-vm-deployment.yml \
  --ask-vault-pass

# Or use vault password file
echo "your-vault-password" > .vault-pass
ansible-playbook ansible/proxmox-vm-deployment.yml \
  --vault-password-file .vault-pass

# IMPORTANT: Add .vault-pass to .gitignore!
echo ".vault-pass" >> .gitignore
```

#### Create new vault file:

```bash
# Create encrypted vault file
ansible-vault create ansible/vault.yml

# Edit encrypted file
ansible-vault edit ansible/vault.yml

# View without editing
ansible-vault view ansible/vault.yml

# Decrypt temporarily (creates .yml file)
ansible-vault decrypt ansible/vault.yml
# Edit the file...
# Re-encrypt it
ansible-vault encrypt ansible/vault.yml
```

### Reference Vault Variables in Playbooks

```yaml
- name: "Use vault credentials"
  hosts: proxmox_nodes
  vars_files:
    - ansible/vault.yml
  tasks:
    - name: "Connect to Proxmox"
      uri:
        url: "https://{{ proxmox_host }}:8006/api2/json/version"
        user: "{{ proxmox_credentials.pve_api_user }}"
        password: "{{ proxmox_credentials.pve_root_password }}"
```

## Secrets Categories

### Proxmox Infrastructure

```env
PROXMOX_HOST=192.168.7.13
PROXMOX_USER=root
PROXMOX_PASSWORD=Hpy@7400adm        # ⚠️ SENSITIVE - use .env only
PROXMOX_API_TOKEN=xxx               # ⚠️ SENSITIVE - use vault
```

### SSH Keys and Passphrases

```env
SSH_KEY_PATH=~/.ssh/id_rsa
SSH_KEY_PASSPHRASE=                 # ⚠️ SENSITIVE - use vault
```

Store actual SSH keys in `~/.ssh/` with proper permissions:

```bash
chmod 700 ~/.ssh
chmod 600 ~/.ssh/id_rsa
chmod 644 ~/.ssh/id_rsa.pub
```

### DNS Server Credentials

```env
DNS01_USER=debian
DNS01_PASS=                          # ⚠️ Prefer SSH key auth (empty)
```

Use SSH key-based authentication instead of passwords when possible.

## Vault Structure Example

```yaml
---
proxmox_credentials:
  pve_root_user: "root"
  pve_root_password: "YOUR_PASSWORD"
  pve_api_user: "root@pam"
  pve_api_token_secret: "YOUR_TOKEN"

ssh_keys:
  builder_vm_ssh_key: |
    -----BEGIN RSA PRIVATE KEY-----
    ...key content...
    -----END RSA PRIVATE KEY-----
  builder_vm_ssh_pub: "ssh-rsa AAAAB3..."

dns_server_credentials:
  ssh_user: "debian"
  sudo_password: ""

network_secrets:
  dns_server_primary: "192.168.0.250"
```

## Security Best Practices

### ✅ DO

- ✅ Keep `.env` in `.gitignore`
- ✅ Keep `ansible/vault.yml` encrypted (encrypted files can be in git)
- ✅ Use SSH key-based authentication (no passwords when possible)
- ✅ Rotate secrets regularly
- ✅ Use unique, strong passwords
- ✅ Limit vault password access (keep separate from git passwords)
- ✅ Use environment variables for CI/CD pipelines
- ✅ Store vault password in secure CI/CD secret manager (GitHub Actions, GitLab CI, etc.)

### ❌ DON'T

- ❌ Commit `.env` files to git
- ❌ Commit unencrypted `vault.yml` files
- ❌ Commit SSH private keys to git
- ❌ Store vault password in code or config files
- ❌ Use the same password for multiple services
- ❌ Share vault password via email or chat
- ❌ Leave passwords in shell history
  ```bash
  # BAD - password visible in history
  ansible-playbook play.yml -e "proxmox_password=MyPassword123"

  # GOOD - use vault or .env
  export PROXMOX_PASSWORD="MyPassword123"
  ansible-playbook play.yml -e "proxmox_password=${PROXMOX_PASSWORD}"
  ```

## CI/CD Pipeline Secrets

### GitHub Actions

```yaml
name: Deploy
on: [push]

jobs:
  deploy:
    runs-on: ubuntu-latest
    env:
      PROXMOX_PASSWORD: ${{ secrets.PROXMOX_PASSWORD }}
      VAULT_PASSWORD: ${{ secrets.VAULT_PASSWORD }}
    steps:
      - uses: actions/checkout@v2
      - name: Create vault password file
        run: echo "${{ secrets.VAULT_PASSWORD }}" > .vault-pass
      - name: Run Ansible
        run: |
          ansible-playbook ansible/proxmox-management.yml \
            --vault-password-file .vault-pass
```

### GitLab CI

```yaml
deploy:
  stage: deploy
  variables:
    PROXMOX_PASSWORD: $PROXMOX_PASSWORD
    VAULT_PASSWORD: $VAULT_PASSWORD
  script:
    - echo $VAULT_PASSWORD > .vault-pass
    - ansible-playbook ansible/proxmox-management.yml --vault-password-file .vault-pass
  only:
    - main
```

## Troubleshooting

### Vault Password Issues

```bash
# Error: Decryption failed
# Solution: Verify you're using the correct vault password

# Error: Vault file not found
# Solution: Check path and ensure .vault.yml exists (encrypted)

# Can't remember vault password?
# Solution: Decrypt with old password, change password, re-encrypt
ansible-vault rekey ansible/vault.yml
```

### Secret Leaks

If a secret has been leaked:

1. **Immediately revoke the credential**
   ```bash
   # Change Proxmox root password
   # Rotate SSH keys
   # Regenerate API tokens
   ```

2. **Remove from git history**
   ```bash
   # Option 1: Using git-filter-branch (careful!)
   git filter-branch --tree-filter 'rm -f .env' HEAD

   # Option 2: Using BFG (safer)
   bfg --delete-files .env --no-blob-protection
   git reflog expire --all --expire=now
   git gc --prune=now --aggressive
   ```

3. **Force push to origin** (only if repo not widely shared)
   ```bash
   git push origin HEAD --force
   ```

4. **Update all credentials**
   - Change Proxmox passwords
   - Rotate SSH keys
   - Revoke API tokens
   - Update CI/CD secrets

## File Permissions

Ensure proper file permissions for sensitive data:

```bash
# .env file - readable only by owner
chmod 600 .env

# SSH directory
chmod 700 ~/.ssh
chmod 600 ~/.ssh/id_rsa
chmod 644 ~/.ssh/id_rsa.pub

# Vault password file (if used)
chmod 600 .vault-pass
```

## Related Documentation

- [Ansible Vault Documentation](https://docs.ansible.com/ansible/latest/user_guide/vault.html)
- [Proxmox API Authentication](https://pve.proxmox.com/wiki/Proxmox_VE_API)
- [SSH Key Management Best Practices](https://linux-audit.com/linux-security-guide-for-hardening-ssh/)

## Summary

| Secret Type | Storage | Method | Auto-rotated |
|-------------|---------|--------|--------------|
| Proxmox Root Password | Vault | Encrypted | No - manual |
| SSH Private Key | ~/.ssh/ | File permissions (600) | No - manual |
| API Tokens | Vault | Encrypted | Yes - recommended |
| Database Passwords | Vault | Encrypted | No - manual |
| .env variables | .env | gitignored | No - local only |
| Vault Password | SecurePass/Manager | Secure storage | No - keep safe |

Always remember: **Secrets are sensitive. Treat them with care. Encrypt them. Never commit them to version control.**
