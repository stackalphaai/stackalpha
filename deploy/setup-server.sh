#!/bin/bash
# StackAlpha Backend Server Setup Script
# Server: 13.60.194.161 (api.stackalpha.xyz)
# Run: sudo bash setup-server.sh

set -e

APP_USER="stackalpha"
APP_DIR="/home/$APP_USER/backend"
LOG_DIR="/var/log/stackalpha"
RUN_DIR="/var/run/stackalpha"

echo "=============================================="
echo "StackAlpha Backend Server Setup"
echo "=============================================="

# Check root
if [ "$EUID" -ne 0 ]; then
    echo "Please run as root: sudo bash setup-server.sh"
    exit 1
fi

# System updates
echo "[1/10] Updating system..."
apt-get update && apt-get upgrade -y

# Install dependencies
echo "[2/10] Installing dependencies..."
apt-get install -y \
    python3.11 \
    python3.11-venv \
    python3.11-dev \
    python3-pip \
    postgresql \
    postgresql-contrib \
    redis-server \
    nginx \
    certbot \
    python3-certbot-nginx \
    git \
    curl \
    ufw \
    fail2ban \
    supervisor

# Create user
echo "[3/10] Creating application user..."
if ! id "$APP_USER" &>/dev/null; then
    useradd -m -s /bin/bash $APP_USER
    echo "$APP_USER ALL=(ALL) NOPASSWD: /bin/systemctl restart stackalpha-*, /bin/systemctl reload stackalpha-*, /bin/systemctl status stackalpha-*" >> /etc/sudoers
fi

# Create directories
echo "[4/10] Creating directories..."
mkdir -p $LOG_DIR $RUN_DIR /home/$APP_USER/.ssh
chown -R $APP_USER:$APP_USER $LOG_DIR $RUN_DIR /home/$APP_USER
chmod 700 /home/$APP_USER/.ssh

# Configure PostgreSQL
echo "[5/10] Configuring PostgreSQL..."
sudo -u postgres psql -c "CREATE USER stackalpha WITH PASSWORD 'CHANGE_THIS_PASSWORD';" 2>/dev/null || true
sudo -u postgres psql -c "CREATE DATABASE stackalpha OWNER stackalpha;" 2>/dev/null || true
sudo -u postgres psql -c "GRANT ALL PRIVILEGES ON DATABASE stackalpha TO stackalpha;" 2>/dev/null || true

# Configure Redis
echo "[6/10] Configuring Redis..."
systemctl enable redis-server
systemctl start redis-server

# Configure firewall
echo "[7/10] Configuring firewall..."
ufw default deny incoming
ufw default allow outgoing
ufw allow ssh
ufw allow http
ufw allow https
ufw --force enable

# Configure Fail2Ban
echo "[8/10] Configuring Fail2Ban..."
systemctl enable fail2ban
systemctl start fail2ban

# Create systemd services
echo "[9/10] Creating systemd services..."

# API Service
cat > /etc/systemd/system/stackalpha-api.service << 'EOF'
[Unit]
Description=StackAlpha API
After=network.target postgresql.service redis.service

[Service]
Type=simple
User=stackalpha
Group=stackalpha
WorkingDirectory=/home/stackalpha/backend
Environment="PATH=/home/stackalpha/backend/venv/bin"
EnvironmentFile=/home/stackalpha/backend/.env
ExecStart=/home/stackalpha/backend/venv/bin/gunicorn app.main:app -w 4 -k uvicorn.workers.UvicornWorker -b 127.0.0.1:8000
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

# Celery Worker Service
cat > /etc/systemd/system/stackalpha-celery-worker.service << 'EOF'
[Unit]
Description=StackAlpha Celery Worker
After=network.target redis.service

[Service]
Type=simple
User=stackalpha
Group=stackalpha
WorkingDirectory=/home/stackalpha/backend
Environment="PATH=/home/stackalpha/backend/venv/bin"
EnvironmentFile=/home/stackalpha/backend/.env
ExecStart=/home/stackalpha/backend/venv/bin/celery -A app.workers.celery_app worker --loglevel=info
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF

# Celery Beat Service
cat > /etc/systemd/system/stackalpha-celery-beat.service << 'EOF'
[Unit]
Description=StackAlpha Celery Beat
After=network.target redis.service

[Service]
Type=simple
User=stackalpha
Group=stackalpha
WorkingDirectory=/home/stackalpha/backend
Environment="PATH=/home/stackalpha/backend/venv/bin"
EnvironmentFile=/home/stackalpha/backend/.env
ExecStart=/home/stackalpha/backend/venv/bin/celery -A app.workers.celery_app beat --loglevel=info
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload

# Configure Nginx
echo "[10/10] Configuring Nginx..."
cat > /etc/nginx/sites-available/stackalpha-api << 'EOF'
server {
    listen 80;
    server_name api.stackalpha.xyz;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_connect_timeout 60s;
        proxy_send_timeout 60s;
        proxy_read_timeout 60s;
    }
}
EOF

ln -sf /etc/nginx/sites-available/stackalpha-api /etc/nginx/sites-enabled/
rm -f /etc/nginx/sites-enabled/default
nginx -t && systemctl reload nginx

echo ""
echo "=============================================="
echo "Server setup complete!"
echo "=============================================="
echo ""
echo "IMPORTANT: Change the PostgreSQL password!"
echo "sudo -u postgres psql -c \"ALTER USER stackalpha WITH PASSWORD 'your_secure_password';\""
echo ""
