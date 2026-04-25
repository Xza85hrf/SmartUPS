#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────────────
# SmartUPS — one-command installer for Raspberry Pi
#
# Usage:
#   chmod +x setup.sh
#   ./setup.sh              # interactive install (venv + deps + systemd)
#   ./setup.sh --no-service # skip systemd service installation
#   ./setup.sh --uninstall  # remove systemd services
# ──────────────────────────────────────────────────────────────────────
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="$SCRIPT_DIR/venv"
SERVICE_SRC="$SCRIPT_DIR/systemd/smartups.service"
SERVICE_DST="/etc/systemd/system/smartups.service"
LOG_DIR="/var/log/smartups"
CURRENT_USER="$(whoami)"

# ── Colors ───────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
CYAN='\033[0;36m'; NC='\033[0m'

info()  { echo -e "${CYAN}[INFO]${NC}  $*"; }
ok()    { echo -e "${GREEN}[OK]${NC}    $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC}  $*"; }
fail()  { echo -e "${RED}[ERROR]${NC} $*"; exit 1; }

# ── Uninstall ────────────────────────────────────────────────────────
if [[ "${1:-}" == "--uninstall" ]]; then
    info "Stopping and removing SmartUPS services..."
    sudo systemctl disable --now smartups.service 2>/dev/null || true
    sudo rm -f "$SERVICE_DST"
    sudo systemctl daemon-reload
    ok "systemd service removed."
    info "Virtual environment and code left in place. Delete manually if desired:"
    info "  rm -rf $SCRIPT_DIR"
    exit 0
fi

INSTALL_SERVICE=true
if [[ "${1:-}" == "--no-service" ]]; then
    INSTALL_SERVICE=false
fi

echo ""
echo -e "${CYAN}╔══════════════════════════════════════════════╗${NC}"
echo -e "${CYAN}║        SmartUPS Installer for Raspberry Pi   ║${NC}"
echo -e "${CYAN}╚══════════════════════════════════════════════╝${NC}"
echo ""

# ── Step 1: Check prerequisites ──────────────────────────────────────
info "Checking prerequisites..."

command -v python3 >/dev/null 2>&1 || fail "python3 not found. Install with: sudo apt install python3"

# Check python3-venv is available
if ! python3 -m venv --help >/dev/null 2>&1; then
    warn "python3-venv not installed. Installing..."
    sudo apt update -qq && sudo apt install -y python3-venv
fi

ok "Python $(python3 --version 2>&1 | awk '{print $2}') found."

# ── Step 2: Check I2C ────────────────────────────────────────────────
info "Checking I2C..."
if [ -e /dev/i2c-1 ]; then
    ok "I2C bus /dev/i2c-1 is available."
else
    warn "I2C bus /dev/i2c-1 not found!"
    echo ""
    echo "  Enable I2C with:  sudo raspi-config"
    echo "  Navigate to:      Interface Options -> I2C -> Enable"
    echo "  Then reboot:      sudo reboot"
    echo ""
    read -rp "Continue anyway? (y/N) " ans
    [[ "$ans" =~ ^[Yy] ]] || exit 1
fi

# Check if user is in i2c group
if groups "$CURRENT_USER" | grep -qw i2c; then
    ok "User '$CURRENT_USER' is in the i2c group."
else
    warn "User '$CURRENT_USER' is NOT in the i2c group."
    info "Adding to i2c group (you may need to log out and back in)..."
    sudo usermod -aG i2c "$CURRENT_USER"
    ok "Added '$CURRENT_USER' to i2c group. Log out/in or reboot for it to take effect."
fi

# ── Step 3: Create virtual environment ───────────────────────────────
info "Setting up Python virtual environment..."
if [ -d "$VENV_DIR" ]; then
    ok "Virtual environment already exists at $VENV_DIR"
else
    python3 -m venv "$VENV_DIR"
    ok "Created virtual environment at $VENV_DIR"
fi

# ── Step 4: Install dependencies ────────────────────────────────────
info "Installing Python dependencies..."
"$VENV_DIR/bin/pip" install --upgrade pip -q
"$VENV_DIR/bin/pip" install -r "$SCRIPT_DIR/requirements.txt" -q
ok "Dependencies installed."

# ── Step 5: Quick sanity check ───────────────────────────────────────
info "Running quick sanity check..."
if "$VENV_DIR/bin/python" -c "from smartups import __version__; print(f'SmartUPS v{__version__}')"; then
    ok "Import check passed."
else
    fail "Import check failed — see errors above."
fi

# ── Step 6: Install systemd service (optional) ──────────────────────
if $INSTALL_SERVICE; then
    echo ""
    info "Installing systemd service for auto-start on boot..."

    # Create log directory
    sudo mkdir -p "$LOG_DIR"
    sudo chown "$CURRENT_USER:$CURRENT_USER" "$LOG_DIR"

    # Generate service file with correct paths and user
    VENV_PYTHON="$VENV_DIR/bin/python"
    SMARTUPS_PY="$SCRIPT_DIR/SmartUPS.py"

    sudo tee "$SERVICE_DST" > /dev/null <<UNIT
[Unit]
Description=SmartUPS — Waveshare UPS Module 3S monitor + graceful shutdown
Documentation=https://github.com/Xza85hrf/SmartUPS
After=multi-user.target network-online.target
Wants=network-online.target

[Service]
User=$CURRENT_USER
Group=i2c
Type=simple

WorkingDirectory=$SCRIPT_DIR

ExecStart=$VENV_PYTHON $SMARTUPS_PY --daemon \\
          --shutdown-threshold 20 --shutdown-consecutive 3 \\
          --csv-file $LOG_DIR/ina219_data_log.csv \\
          --log-file $LOG_DIR/smartups.log

Restart=on-failure
RestartSec=10s

CPUQuota=10%
MemoryMax=128M

NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=read-only
ReadWritePaths=$LOG_DIR
PrivateTmp=true
ProtectKernelTunables=true
ProtectKernelModules=true
ProtectControlGroups=true
RestrictRealtime=true
AmbientCapabilities=CAP_SYS_BOOT
CapabilityBoundingSet=CAP_SYS_BOOT

[Install]
WantedBy=multi-user.target
UNIT

    sudo systemctl daemon-reload
    sudo systemctl enable smartups.service
    ok "Service installed and enabled."

    read -rp "Start SmartUPS service now? (Y/n) " ans
    if [[ ! "$ans" =~ ^[Nn] ]]; then
        sudo systemctl start smartups.service
        sleep 2
        if systemctl is-active --quiet smartups.service; then
            ok "SmartUPS service is running!"
        else
            warn "Service may have failed to start. Check with:"
            echo "  sudo systemctl status smartups.service"
            echo "  journalctl -u smartups.service -f"
        fi
    fi
fi

# ── Done ─────────────────────────────────────────────────────────────
echo ""
echo -e "${GREEN}╔══════════════════════════════════════════════╗${NC}"
echo -e "${GREEN}║          Setup complete!                     ║${NC}"
echo -e "${GREEN}╚══════════════════════════════════════════════╝${NC}"
echo ""
echo "  Quick start (interactive):  $VENV_DIR/bin/python SmartUPS.py"
echo "  With live plot:             $VENV_DIR/bin/python SmartUPS.py --show-plot"
echo "  Service status:             sudo systemctl status smartups.service"
echo "  View live logs:             journalctl -u smartups.service -f"
echo "  Uninstall service:          ./setup.sh --uninstall"
echo ""
