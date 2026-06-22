#!/usr/bin/env bash
# Auctorum bring-up — idempotent provisioner.
#
# Run on the Auctorum PC (Ubuntu 22.04+ assumed):
#
#   curl -sL https://raw.githubusercontent.com/<user>/kee/main/scripts/auctorum/provision.sh | bash
#
# OR (more typical) — clone the kee repo first then:
#
#   cd ~/kee && bash scripts/auctorum/provision.sh
#
# Skips steps that are already done. Logs everything to
# /var/log/keeprovision.log so you can grep for what failed.

set -e

LOG=/var/log/keeprovision.log
sudo touch "$LOG" && sudo chown "$USER" "$LOG"
exec > >(tee -a "$LOG") 2>&1

echo "──────────────────────────────────────────────"
echo "Kee Auctorum provisioner — $(date -Iseconds)"
echo "──────────────────────────────────────────────"

USER_NAME="${SUDO_USER:-$USER}"
TAILSCALE_HOSTNAME="${TAILSCALE_HOSTNAME:-auctorum}"
KEE_REPO="${KEE_REPO:-https://github.com/cocopsn/kee.git}"
KEE_DIR="${KEE_DIR:-$HOME/kee}"
VAULT_DIR="${VAULT_DIR:-$HOME/kee-vault}"

# ── Phase 1: base packages ────────────────────────────────────────────
echo "[1/9] base packages"
sudo apt update -qq
sudo apt install -y --no-install-recommends \
    python3.12 python3.12-venv python3-pip \
    git curl wget ca-certificates \
    htop nvtop iotop tmux jq \
    ufw fail2ban build-essential \
    syncthing

# ── Phase 2: timezone + swap ──────────────────────────────────────────
echo "[2/9] timezone + swap"
sudo timedatectl set-timezone America/Mexico_City || true

if ! swapon --show | grep -q '^/swap'; then
    echo "Creating 4G swap…"
    sudo fallocate -l 4G /swapfile
    sudo chmod 600 /swapfile
    sudo mkswap /swapfile
    sudo swapon /swapfile
    echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
fi

# ── Phase 3: firewall (Tailscale-friendly) ─────────────────────────────
echo "[3/9] ufw + fail2ban"
sudo ufw allow ssh
sudo ufw allow from 100.64.0.0/10                  # Tailscale CGNAT
sudo ufw allow 22000/tcp                            # Syncthing sync
sudo ufw allow 21027/udp                            # Syncthing discovery
echo y | sudo ufw enable || true
sudo systemctl enable --now fail2ban

# ── Phase 4: Tailscale (skip if already up) ────────────────────────────
echo "[4/9] tailscale"
if ! command -v tailscale &>/dev/null; then
    curl -fsSL https://tailscale.com/install.sh | sh
fi
if ! sudo tailscale status &>/dev/null; then
    echo "Tailscale not joined yet. Run manually:"
    echo "    sudo tailscale up --hostname=$TAILSCALE_HOSTNAME --ssh"
    echo "Then re-run this script."
    exit 1
fi

# ── Phase 5: Ollama ────────────────────────────────────────────────────
echo "[5/9] ollama"
if ! command -v ollama &>/dev/null; then
    curl -fsSL https://ollama.com/install.sh | sh
fi
sudo mkdir -p /etc/systemd/system/ollama.service.d
cat <<EOF | sudo tee /etc/systemd/system/ollama.service.d/override.conf
[Service]
Environment="OLLAMA_HOST=0.0.0.0:11434"
Environment="OLLAMA_KEEP_ALIVE=24h"
EOF
sudo systemctl daemon-reload
sudo systemctl enable --now ollama
sleep 2
ollama pull nomic-embed-text || true
# qwen3:8b is heavy — pull only if you actually want failover inference
# ollama pull qwen3:8b

# ── Phase 6: ChromaDB ──────────────────────────────────────────────────
echo "[6/9] chromadb"
sudo mkdir -p /opt/chroma /var/lib/chromadb
sudo chown -R "$USER_NAME":"$USER_NAME" /opt/chroma /var/lib/chromadb
if [ ! -d /opt/chroma/venv ]; then
    python3.12 -m venv /opt/chroma/venv
fi
/opt/chroma/venv/bin/pip install -qU pip
/opt/chroma/venv/bin/pip install -q "chromadb>=0.5,<0.6"

cat <<EOF | sudo tee /etc/systemd/system/chromadb.service
[Unit]
Description=ChromaDB server
After=network.target

[Service]
Type=simple
User=$USER_NAME
ExecStart=/opt/chroma/venv/bin/chroma run --host 0.0.0.0 --port 8000 --path /var/lib/chromadb
Restart=always
RestartSec=5
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
EOF
sudo systemctl daemon-reload
sudo systemctl enable --now chromadb

# ── Phase 7: reranker + vision + health (need the kee repo) ────────────
echo "[7/9] kee services (reranker + vision + health)"
if [ ! -d "$KEE_DIR" ]; then
    git clone "$KEE_REPO" "$KEE_DIR"
fi
cd "$KEE_DIR"
git pull --ff-only || true

for svc in reranker keevision keehealth; do
    sudo mkdir -p /opt/$svc
    sudo chown "$USER_NAME":"$USER_NAME" /opt/$svc
    if [ ! -d /opt/$svc/venv ]; then
        python3.12 -m venv /opt/$svc/venv
    fi
done

# Reranker
/opt/reranker/venv/bin/pip install -qU pip
/opt/reranker/venv/bin/pip install -q fastapi "uvicorn[standard]" flashrank pydantic
cp "$KEE_DIR/scripts/auctorum/reranker_server.py" /opt/reranker/server.py

# Vision (model pull is optional — service starts and lazy-loads)
/opt/keevision/venv/bin/pip install -qU pip
/opt/keevision/venv/bin/pip install -q fastapi "uvicorn[standard]" httpx pydantic
cp "$KEE_DIR/scripts/auctorum/vision_server.py" /opt/keevision/server.py

# Health
/opt/keehealth/venv/bin/pip install -qU pip
/opt/keehealth/venv/bin/pip install -q fastapi "uvicorn[standard]" httpx psutil
cp "$KEE_DIR/scripts/auctorum/health_server.py" /opt/keehealth/server.py

# Systemd units
for svc in reranker keevision keehealth; do
    PORT_VAR=""
    case $svc in
        reranker)  PORT=8002 ;;
        keevision) PORT=8003 ;;
        keehealth) PORT=8080 ;;
    esac
    cat <<EOF | sudo tee /etc/systemd/system/$svc.service
[Unit]
Description=Kee $svc service
After=network.target

[Service]
Type=simple
User=$USER_NAME
ExecStart=/opt/$svc/venv/bin/python /opt/$svc/server.py
Restart=always
RestartSec=5
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
EOF
    sudo ufw allow from 100.64.0.0/10 to any port $PORT
done

sudo systemctl daemon-reload
sudo systemctl enable --now reranker keevision keehealth

# ── Phase 8: Syncthing ─────────────────────────────────────────────────
echo "[8/9] syncthing"
mkdir -p "$VAULT_DIR"
systemctl --user enable syncthing.service || sudo systemctl enable --now syncthing@$USER_NAME
echo "Syncthing UI: ssh -L 8384:localhost:8384 $USER_NAME@$TAILSCALE_HOSTNAME"
echo "Then open http://localhost:8384 — pair with Alienware (Settings → Show ID)."
echo "Add folder $VAULT_DIR shared with the Alienware vault."

# ── Phase 9: Smoke ─────────────────────────────────────────────────────
echo "[9/9] smoke tests"
sleep 3
echo
echo "── ChromaDB ──"; curl -fsS http://localhost:8000/api/v1/heartbeat || echo FAIL
echo
echo "── Ollama ──";   curl -fsS http://localhost:11434/api/tags | jq '.models | length' || echo FAIL
echo
echo "── Reranker ──"; curl -fsS http://localhost:8002/health || echo FAIL
echo
echo "── Vision ──";   curl -fsS http://localhost:8003/health || echo FAIL
echo
echo "── Health ──";   curl -fsS http://localhost:8080/health | jq '{ok, host}' || echo FAIL

echo
echo "──────────────────────────────────────────────"
echo "Provisioner done — $(date -Iseconds)"
echo "Log: $LOG"
echo
echo "On the Alienware, set in .env:"
echo "    AUCTORUM_HOST=$TAILSCALE_HOSTNAME"
echo "    AUCTORUM_OLLAMA=http://$TAILSCALE_HOSTNAME:11434"
echo "    CHROMADB_HOST=http://$TAILSCALE_HOSTNAME:8000"
echo "    KEE_RERANKER_URL=http://$TAILSCALE_HOSTNAME:8002"
echo "    KEE_VISION_URL=http://$TAILSCALE_HOSTNAME:8003"
echo "    KEE_WORKER_HEALTH_URL=http://$TAILSCALE_HOSTNAME:8080"
echo
echo "Then: python -m kee.main check"
echo "──────────────────────────────────────────────"
