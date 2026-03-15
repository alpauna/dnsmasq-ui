#!/bin/bash
# Secrets Setup Script
# Helps you initialize and configure secrets safely

set -e

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

log() { echo -e "${BLUE}[*]${NC} $*"; }
success() { echo -e "${GREEN}✓${NC} $*"; }
error() { echo -e "${RED}✗${NC} $*"; exit 1; }
warning() { echo -e "${YELLOW}⚠${NC} $*"; }

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

header() {
    echo ""
    echo -e "${BLUE}╔════════════════════════════════════════════════════════════╗${NC}"
    echo -e "${BLUE}║${NC} $1"
    echo -e "${BLUE}╚════════════════════════════════════════════════════════════╝${NC}"
    echo ""
}

# Check if ansible is installed
if ! command -v ansible-vault &> /dev/null; then
    warning "Ansible not installed. Install with: pip install ansible"
fi

header "dnsmasq-ui Secrets Setup"

echo "This script will help you securely configure secrets for your infrastructure."
echo ""

# Step 1: Create .env file
echo "Step 1: Environment Variables (.env)"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""

if [ -f "$SCRIPT_DIR/.env" ]; then
    warning ".env file already exists"
    read -p "Overwrite it? [y/N]: " overwrite
    if [[ ! $overwrite =~ ^[Yy]$ ]]; then
        log "Skipping .env creation"
    else
        cp "$SCRIPT_DIR/.env.example" "$SCRIPT_DIR/.env"
        success ".env created"
    fi
else
    cp "$SCRIPT_DIR/.env.example" "$SCRIPT_DIR/.env"
    success ".env created from template"
fi

# Step 2: Add to .gitignore
echo ""
echo "Step 2: Update .gitignore"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""

if [ -f "$SCRIPT_DIR/.gitignore" ]; then
    if grep -q "^.env$" "$SCRIPT_DIR/.gitignore"; then
        log ".env already in .gitignore"
    else
        warning ".env not in .gitignore. Adding it..."
        echo ".env" >> "$SCRIPT_DIR/.gitignore"
        success "Added .env to .gitignore"
    fi
else
    echo ".env" > "$SCRIPT_DIR/.gitignore"
    success "Created .gitignore with .env"
fi

# Also add the secrets patterns
if [ ! grep -q "ansible/vault.yml" "$SCRIPT_DIR/.gitignore" 2>/dev/null ]; then
    cat "$SCRIPT_DIR/.gitignore-secrets" >> "$SCRIPT_DIR/.gitignore" 2>/dev/null || true
    success "Added secret patterns to .gitignore"
fi

# Step 3: Set up Ansible Vault
echo ""
echo "Step 3: Ansible Vault Setup"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""

if [ -f "$SCRIPT_DIR/ansible/vault.yml" ]; then
    if file "$SCRIPT_DIR/ansible/vault.yml" | grep -q "ASCII text"; then
        warning "vault.yml is not encrypted!"
        read -p "Encrypt it now? [y/N]: " encrypt_vault
        if [[ $encrypt_vault =~ ^[Yy]$ ]]; then
            ansible-vault encrypt "$SCRIPT_DIR/ansible/vault.yml"
            success "vault.yml encrypted"
        fi
    else
        log "vault.yml already encrypted"
    fi
else
    read -p "Create new vault.yml? [y/N]: " create_vault
    if [[ $create_vault =~ ^[Yy]$ ]]; then
        cp "$SCRIPT_DIR/ansible/.vault-example" "$SCRIPT_DIR/ansible/vault.yml"

        echo ""
        log "Edit vault.yml to add your secrets:"
        echo "  nano $SCRIPT_DIR/ansible/vault.yml"
        echo ""

        read -p "Open in editor now? [y/N]: " edit_now
        if [[ $edit_now =~ ^[Yy]$ ]]; then
            ${EDITOR:-nano} "$SCRIPT_DIR/ansible/vault.yml"
        fi

        echo ""
        log "Encrypting vault.yml..."
        ansible-vault encrypt "$SCRIPT_DIR/ansible/vault.yml"
        success "vault.yml created and encrypted"
    fi
fi

# Step 4: SSH Key Setup
echo ""
echo "Step 4: SSH Key Configuration"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""

if [ -f ~/.ssh/id_rsa ]; then
    success "SSH key found at ~/.ssh/id_rsa"

    # Check permissions
    perms=$(stat -f %A ~/.ssh/id_rsa 2>/dev/null || stat -c %a ~/.ssh/id_rsa 2>/dev/null)
    if [ "$perms" != "600" ]; then
        warning "SSH key has incorrect permissions (current: $perms, should be 600)"
        read -p "Fix permissions? [y/N]: " fix_perms
        if [[ $fix_perms =~ ^[Yy]$ ]]; then
            chmod 600 ~/.ssh/id_rsa
            chmod 700 ~/.ssh
            success "SSH key permissions fixed"
        fi
    fi
else
    warning "SSH key not found at ~/.ssh/id_rsa"
    read -p "Generate new SSH key? [y/N]: " gen_key
    if [[ $gen_key =~ ^[Yy]$ ]]; then
        mkdir -p ~/.ssh
        ssh-keygen -t rsa -b 4096 -f ~/.ssh/id_rsa -N "" -C "dnsmasq-ui"
        chmod 600 ~/.ssh/id_rsa
        chmod 700 ~/.ssh
        success "SSH key generated"
    fi
fi

# Step 5: File permissions check
echo ""
echo "Step 5: File Permissions"
echo "━━━━━━━━━━━━━━━━━━━━━━"
echo ""

# Check .env permissions
if [ -f "$SCRIPT_DIR/.env" ]; then
    perms=$(stat -f %A "$SCRIPT_DIR/.env" 2>/dev/null || stat -c %a "$SCRIPT_DIR/.env" 2>/dev/null)
    if [ "$perms" != "600" ]; then
        warning ".env has permissions $perms (should be 600)"
        chmod 600 "$SCRIPT_DIR/.env"
        success ".env permissions fixed"
    else
        log ".env permissions are correct (600)"
    fi
fi

# Step 6: Verify git won't commit secrets
echo ""
echo "Step 6: Git Security Check"
echo "━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""

cd "$SCRIPT_DIR"

if git rev-parse --git-dir > /dev/null 2>&1; then
    # Check if secrets are staged
    if git diff --cached --name-only | grep -E "\.env|vault\.yml|\.key|\.pem" > /dev/null 2>&1; then
        error "Secrets are staged in git! Run: git reset .env ansible/vault.yml"
    fi

    # Check if secrets are already committed
    if git log --all --pretty=format: --name-only | grep -E "\.env|vault\.yml" | grep -v example > /dev/null 2>&1; then
        warning "Secrets may have been committed to git history"
        echo "Run: git log --all --oneline -- .env ansible/vault.yml"
    else
        success "No secrets found in git history"
    fi
else
    log "Not a git repository, skipping git checks"
fi

# Step 7: Summary and next steps
echo ""
header "Setup Complete! ✓"

echo "Summary:"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
echo "✓ .env file created (add your Proxmox credentials here)"
echo "✓ .gitignore updated (prevents accidental secret commits)"
echo "✓ Ansible vault configured (for encrypted secrets)"
echo "✓ SSH keys verified (for authentication)"
echo "✓ File permissions verified (secrets protected)"
echo "✓ Git security checked (no secrets in git)"
echo ""

echo "Next Steps:"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
echo "1. Edit your secrets:"
echo "   nano .env                        # Add Proxmox credentials here"
echo ""
echo "2. Update .env with your values:"
echo "   PROXMOX_HOST=192.168.7.13"
echo "   PROXMOX_PASSWORD=your_password"
echo "   PROXMOX_USER=root"
echo "   SSH_USER=debian"
echo ""
echo "3. Set up Ansible vault (optional but recommended):"
echo "   ansible-vault create ansible/vault.yml"
echo "   ansible-vault edit ansible/vault.yml"
echo ""
echo "4. Verify setup:"
echo "   source .env"
echo "   echo \$PROXMOX_HOST"
echo ""
echo "5. Test Proxmox connection:"
echo "   source .env"
echo "   ssh root@\$PROXMOX_HOST"
echo ""
echo "6. Run Ansible playbooks:"
echo "   source .env"
echo "   ansible-playbook ansible/proxmox-management.yml"
echo ""

echo "Security Reminders:"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
echo "⚠️  Never commit .env to git"
echo "⚠️  Keep vault password secure (not in code/config)"
echo "⚠️  Rotate secrets regularly"
echo "⚠️  Use SSH keys instead of passwords when possible"
echo "⚠️  Clear shell history if you paste secrets: history -c"
echo ""

echo "Documentation:"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
echo "See SECRETS_MANAGEMENT.md for detailed security practices"
echo ""
success "Setup complete!"
