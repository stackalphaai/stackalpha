#!/bin/bash
# StackAlpha Backend Server Setup Script
# Server: 144.126.235.91 (server.stackalpha.xyz)
# Run as root: sudo bash setup-server.sh
#
# Prerequisites: project cloned at /home/stackalpha/stackalpha
#                dependencies already installed

set -e

APP_USER="stackalpha"
PROJECT_DIR="/home/$APP_USER/stackalpha"
BACKEND_DIR="$PROJECT_DIR/backend"
DEPLOY_DIR="$BACKEND_DIR/deploy"
LOG_DIR="/var/log/stackalpha"
RUN_DIR="/var/run/stackalpha"

echo "=============================================="
echo "StackAlpha Backend - Service Setup"
echo "Server: 144.126.235.91"
echo "Domain: server.stackalpha.xyz"
echo "=============================================="

# Check root
if [ "$EUID" -ne 0 ]; then
    echo "Please run as root: sudo bash setup-server.sh"
    exit 1
fi

# Verify project exists
if [ ! -d "$BACKEND_DIR" ]; then
    echo "ERROR: Backend directory not found at $BACKEND_DIR"
    exit 1
fi

echo ""
echo "[1/8] Creating directories..."
mkdir -p $LOG_DIR $RUN_DIR
chown -R $APP_USER:$APP_USER $LOG_DIR $RUN_DIR

# Create tmpfiles.d config so /var/run/stackalpha survives reboots
cat > /etc/tmpfiles.d/stackalpha.conf << EOF
d /var/run/stackalpha 0755 $APP_USER $APP_USER -
EOF

echo "[2/8] Installing Gunicorn in virtualenv..."
if [ -d "$BACKEND_DIR/.venv" ]; then
    echo "  Using .venv (uv virtualenv)"
    $BACKEND_DIR/.venv/bin/pip install gunicorn 2>/dev/null || \
    $BACKEND_DIR/.venv/bin/uv pip install gunicorn 2>/dev/null || \
    echo "  WARNING: Could not install gunicorn. Install manually: cd $BACKEND_DIR && .venv/bin/pip install gunicorn"
elif [ -d "$BACKEND_DIR/venv" ]; then
    echo "  Using venv (traditional virtualenv)"
    $BACKEND_DIR/venv/bin/pip install gunicorn
else
    echo "  WARNING: No virtualenv found. Make sure gunicorn is installed."
fi

echo "[3/8] Copying systemd service files..."
cp $DEPLOY_DIR/stackalpha-api.service /etc/systemd/system/
cp $DEPLOY_DIR/stackalpha-celery-worker.service /etc/systemd/system/
cp $DEPLOY_DIR/stackalpha-celery-beat.service /etc/systemd/system/
echo "  Copied: stackalpha-api.service"
echo "  Copied: stackalpha-celery-worker.service"
echo "  Copied: stackalpha-celery-beat.service"

echo "[4/8] Reloading systemd daemon..."
systemctl daemon-reload

echo "[5/8] Enabling services..."
systemctl enable stackalpha-api
systemctl enable stackalpha-celery-worker
systemctl enable stackalpha-celery-beat
echo "  All services enabled (will start on boot)"

echo "[6/8] Configuring Nginx..."
cp $DEPLOY_DIR/nginx.conf /etc/nginx/sites-available/stackalpha
ln -sf /etc/nginx/sites-available/stackalpha /etc/nginx/sites-enabled/
rm -f /etc/nginx/sites-enabled/default

# Test nginx config
if nginx -t 2>/dev/null; then
    echo "  Nginx config test: OK"
else
    echo "  WARNING: Nginx config test failed."
    echo "  SSL certificates may not exist yet. Run step 7 first."
fi

echo "[7/8] Setting up SSL with Certbot..."
echo ""
echo "  IMPORTANT: Before running certbot, make sure your DNS A record"
echo "  for server.stackalpha.xyz points to 144.126.235.91"
echo ""
echo "  To get SSL certificate, run:"
echo "    sudo certbot --nginx -d server.stackalpha.xyz"
echo ""
echo "  OR for a temporary HTTP-only setup (skip SSL for now):"

# Create temporary HTTP-only nginx config
cat > /etc/nginx/sites-available/stackalpha-http << 'HTTPEOF'
upstream stackalpha_api_http {
    server 127.0.0.1:8000;
    keepalive 32;
}

server {
    listen 80;
    server_name server.stackalpha.xyz;

    location /health {
        proxy_pass http://stackalpha_api_http;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        access_log off;
    }

    location /api/ {
        proxy_pass http://stackalpha_api_http;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
    }

    location /api/v1/ws {
        proxy_pass http://stackalpha_api_http;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 86400s;
        proxy_send_timeout 86400s;
    }

    location /api/v1/webhooks/ {
        proxy_pass http://stackalpha_api_http;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    location /admin {
        proxy_pass http://stackalpha_api_http;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    location /docs {
        proxy_pass http://stackalpha_api_http;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
    }

    location /redoc {
        proxy_pass http://stackalpha_api_http;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
    }

    location /openapi.json {
        proxy_pass http://stackalpha_api_http;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
    }
}
HTTPEOF

echo "  Created HTTP-only fallback config at /etc/nginx/sites-available/stackalpha-http"
echo ""
echo "  To use HTTP-only (no SSL) temporarily:"
echo "    sudo ln -sf /etc/nginx/sites-available/stackalpha-http /etc/nginx/sites-enabled/stackalpha"
echo "    sudo systemctl reload nginx"
echo ""
echo "  Then later, get SSL and switch back:"
echo "    sudo certbot --nginx -d server.stackalpha.xyz"
echo "    sudo ln -sf /etc/nginx/sites-available/stackalpha /etc/nginx/sites-enabled/stackalpha"
echo "    sudo systemctl reload nginx"

echo ""
echo "[8/8] Starting services..."
echo ""

# Start services
echo "  Starting stackalpha-api..."
systemctl start stackalpha-api && echo "    OK" || echo "    FAILED - check: journalctl -u stackalpha-api -n 50"

echo "  Starting stackalpha-celery-worker..."
systemctl start stackalpha-celery-worker && echo "    OK" || echo "    FAILED - check: journalctl -u stackalpha-celery-worker -n 50"

echo "  Starting stackalpha-celery-beat..."
systemctl start stackalpha-celery-beat && echo "    OK" || echo "    FAILED - check: journalctl -u stackalpha-celery-beat -n 50"

# Use HTTP-only config initially (SSL cert might not exist yet)
ln -sf /etc/nginx/sites-available/stackalpha-http /etc/nginx/sites-enabled/stackalpha
nginx -t && systemctl reload nginx && echo "  Nginx reloaded with HTTP-only config: OK" || echo "  Nginx reload: FAILED"

echo ""
echo "=============================================="
echo "Setup complete!"
echo "=============================================="
echo ""
echo "Service status:"
systemctl is-active stackalpha-api && echo "  stackalpha-api: RUNNING" || echo "  stackalpha-api: NOT RUNNING"
systemctl is-active stackalpha-celery-worker && echo "  stackalpha-celery-worker: RUNNING" || echo "  stackalpha-celery-worker: NOT RUNNING"
systemctl is-active stackalpha-celery-beat && echo "  stackalpha-celery-beat: RUNNING" || echo "  stackalpha-celery-beat: NOT RUNNING"
echo ""
echo "Useful commands:"
echo "  sudo systemctl status stackalpha-api"
echo "  sudo systemctl status stackalpha-celery-worker"
echo "  sudo systemctl status stackalpha-celery-beat"
echo "  sudo journalctl -u stackalpha-api -f           # live API logs"
echo "  sudo journalctl -u stackalpha-celery-worker -f  # live worker logs"
echo "  tail -f /var/log/stackalpha/access.log          # Gunicorn access log"
echo "  tail -f /var/log/stackalpha/error.log           # Gunicorn error log"
echo ""
echo "Next steps:"
echo "  1. Verify: curl http://localhost:8000/health"
echo "  2. Set up SSL: sudo certbot --nginx -d server.stackalpha.xyz"
echo "  3. Switch to SSL config: sudo ln -sf /etc/nginx/sites-available/stackalpha /etc/nginx/sites-enabled/stackalpha && sudo systemctl reload nginx"
echo "  4. Test: curl https://server.stackalpha.xyz/health"
echo ""
