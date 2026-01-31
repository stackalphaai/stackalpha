# StackAlpha Backend Deployment Guide

**Server:** 13.60.194.161
**Domain:** api.stackalpha.xyz
**PEM File:** stackalpha.pem

---

## Step 1: SSH into Server

```powershell
# From your local machine (PowerShell)
ssh -i stackalpha.pem ubuntu@13.60.194.161
```

If you get a permission error on Windows:
```powershell
icacls stackalpha.pem /inheritance:r
icacls stackalpha.pem /grant:r "%USERNAME%:R"
ssh -i stackalpha.pem ubuntu@13.60.194.161
```

---

## Step 2: Initial Server Setup

Once connected to the server, run these commands:

```bash
# Update system
sudo apt update && sudo apt upgrade -y

# Install required packages
sudo apt install -y python3.11 python3.11-venv python3.11-dev python3-pip \
    postgresql postgresql-contrib redis-server nginx certbot \
    python3-certbot-nginx git curl ufw fail2ban

# Create directories
sudo mkdir -p /var/log/stackalpha /var/run/stackalpha
sudo chown -R stackalpha:stackalpha /var/log/stackalpha /var/run/stackalpha
```

---

## Step 3: Configure PostgreSQL

```bash
# Create database user and database
sudo -u postgres psql << EOF
CREATE USER stackalpha WITH PASSWORD 'YOUR_SECURE_DB_PASSWORD';
CREATE DATABASE stackalpha OWNER stackalpha;
GRANT ALL PRIVILEGES ON DATABASE stackalpha TO stackalpha;
EOF
```

**Save this password - you'll need it for the .env file!**

---

## Step 4: Configure Redis

```bash
sudo systemctl enable redis-server
sudo systemctl start redis-server

# Verify Redis is running
redis-cli ping
# Should return: PONG
```

---

## Step 5: Clone Repository

```bash
cd /home/stackalpha

# Clone repository
git clone https://github.com/stackalphaai/stackalpha.git
cd stackalpha

# Create virtual environment
python3.11 -m venv venv
source venv/bin/activate

# Install dependencies
pip install --upgrade pip
pip install -r requirements.txt
# OR if using uv:
# pip install uv && uv sync
```

---

## Step 6: Create Environment File

```bash
# In /home/stackalpha/stackalpha
nano .env
```

Add this content (update values):

```env
# Application
APP_NAME=StackAlpha
APP_ENV=production
DEBUG=false
SECRET_KEY=generate-a-random-64-char-string-here
API_VERSION=v1
ALLOWED_HOSTS=["api.stackalpha.xyz","13.60.194.161"]
CORS_ORIGINS=["https://stackalpha.xyz","https://www.stackalpha.xyz"]

# Database
DATABASE_URL=postgresql+asyncpg://stackalpha:YOUR_SECURE_DB_PASSWORD@localhost:5432/stackalpha

# Redis
REDIS_URL=redis://localhost:6379/0

# JWT
JWT_SECRET_KEY=generate-another-random-64-char-string
JWT_ALGORITHM=HS256
ACCESS_TOKEN_EXPIRE_MINUTES=15
REFRESH_TOKEN_EXPIRE_DAYS=7

# Encryption (base64 encoded 32-byte key)
ENCRYPTION_KEY=generate-base64-encoded-32-byte-key

# Email (Zoho)
SMTP_HOST=smtp.zoho.com
SMTP_PORT=587
SMTP_USER=tech@stackalpha.xyz
SMTP_PASSWORD=your-email-password
SMTP_FROM_NAME=StackAlpha
SMTP_FROM_EMAIL=tech@stackalpha.xyz

# Celery
CELERY_BROKER_URL=redis://localhost:6379/1
CELERY_RESULT_BACKEND=redis://localhost:6379/2

# Add your other API keys (Hyperliquid, NOWPayments, Telegram, etc.)
```

Generate random keys:
```bash
# Generate SECRET_KEY
python3 -c "import secrets; print(secrets.token_hex(32))"

# Generate ENCRYPTION_KEY (Fernet key)
python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

---

## Step 7: Run Database Migrations

```bash
cd /home/stackalpha/stackalpha
source venv/bin/activate
alembic upgrade head
```

---

## Step 8: Create Systemd Services

```bash
# Create API service
sudo tee /etc/systemd/system/stackalpha-api.service << 'EOF'
[Unit]
Description=StackAlpha API
After=network.target postgresql.service redis.service

[Service]
Type=simple
User=stackalpha
Group=stackalpha
WorkingDirectory=/home/stackalpha/stackalpha
Environment="PATH=/home/stackalpha/stackalpha/venv/bin"
EnvironmentFile=/home/stackalpha/stackalpha/.env
ExecStart=/home/stackalpha/stackalpha/venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 4
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

# Create Celery Worker service
sudo tee /etc/systemd/system/stackalpha-celery-worker.service << 'EOF'
[Unit]
Description=StackAlpha Celery Worker
After=network.target redis.service

[Service]
Type=simple
User=stackalpha
Group=stackalpha
WorkingDirectory=/home/stackalpha/stackalpha
Environment="PATH=/home/stackalpha/stackalpha/venv/bin"
EnvironmentFile=/home/stackalpha/stackalpha/.env
ExecStart=/home/stackalpha/stackalpha/venv/bin/celery -A app.workers.celery_app worker --loglevel=info
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF

# Create Celery Beat service
sudo tee /etc/systemd/system/stackalpha-celery-beat.service << 'EOF'
[Unit]
Description=StackAlpha Celery Beat
After=network.target redis.service

[Service]
Type=simple
User=stackalpha
Group=stackalpha
WorkingDirectory=/home/stackalpha/stackalpha
Environment="PATH=/home/stackalpha/stackalpha/venv/bin"
EnvironmentFile=/home/stackalpha/stackalpha/.env
ExecStart=/home/stackalpha/stackalpha/venv/bin/celery -A app.workers.celery_app beat --loglevel=info
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF

# Reload systemd
sudo systemctl daemon-reload

# Enable services to start on boot
sudo systemctl enable stackalpha-api stackalpha-celery-worker stackalpha-celery-beat

# Start services
sudo systemctl start stackalpha-api stackalpha-celery-worker stackalpha-celery-beat

# Check status
sudo systemctl status stackalpha-api
```

---

## Step 9: Configure Nginx

```bash
# Create Nginx config
sudo tee /etc/nginx/sites-available/stackalpha-api << 'EOF'
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
        client_max_body_size 10M;
    }
}
EOF

# Enable site
sudo ln -sf /etc/nginx/sites-available/stackalpha-api /etc/nginx/sites-enabled/
sudo rm -f /etc/nginx/sites-enabled/default

# Test config
sudo nginx -t

# Reload Nginx
sudo systemctl reload nginx
```

---

## Step 10: Setup SSL with Let's Encrypt

```bash
sudo certbot --nginx -d api.stackalpha.xyz
```

Follow the prompts. Choose to redirect HTTP to HTTPS.

---

## Step 11: Configure Firewall

```bash
sudo ufw default deny incoming
sudo ufw default allow outgoing
sudo ufw allow ssh
sudo ufw allow http
sudo ufw allow https
sudo ufw enable
```

---

## Step 12: Setup GitHub Actions SSH Key

On your **local machine**, generate an SSH key for GitHub Actions:

```powershell
# Generate key (no passphrase)
ssh-keygen -t ed25519 -f github-actions-key -C "github-actions"
```

This creates:
- `github-actions-key` (private key)
- `github-actions-key.pub` (public key)

**On the server**, add the public key:

```bash
# Add public key to authorized_keys
mkdir -p ~/.ssh
chmod 700 ~/.ssh
nano ~/.ssh/authorized_keys
# Paste the content of github-actions-key.pub
chmod 600 ~/.ssh/authorized_keys
```

---

## Step 13: Configure GitHub Secrets

Go to your GitHub repository -> Settings -> Secrets and variables -> Actions

Add these **secrets**:

| Secret Name | Value |
|-------------|-------|
| `SERVER_HOST` | `13.60.194.161` |
| `SERVER_USER` | `stackalpha` |
| `SSH_PRIVATE_KEY` | Content of `github-actions-key` file (the private key) |

Add this **variable** (Variables tab):

| Variable Name | Value |
|---------------|-------|
| `DEPLOY_ENABLED` | `true` |

---

## Step 14: Configure Sudoers for Deployment

```bash
sudo visudo
```

Add at the end:
```
stackalpha ALL=(ALL) NOPASSWD: /bin/systemctl restart stackalpha-api, /bin/systemctl restart stackalpha-celery-worker, /bin/systemctl restart stackalpha-celery-beat
```

---

## Useful Commands

### Check Service Status
```bash
sudo systemctl status stackalpha-api
sudo systemctl status stackalpha-celery-worker
sudo systemctl status stackalpha-celery-beat
```

### View Logs
```bash
# API logs
sudo journalctl -u stackalpha-api -f

# Celery worker logs
sudo journalctl -u stackalpha-celery-worker -f

# Celery beat logs
sudo journalctl -u stackalpha-celery-beat -f

# Nginx logs
sudo tail -f /var/log/nginx/access.log
sudo tail -f /var/log/nginx/error.log
```

### Restart Services
```bash
sudo systemctl restart stackalpha-api
sudo systemctl restart stackalpha-celery-worker
sudo systemctl restart stackalpha-celery-beat
```

### Manual Deployment
```bash
cd /home/stackalpha/stackalpha
git pull origin main
source venv/bin/activate
pip install -r requirements.txt
alembic upgrade head
sudo systemctl restart stackalpha-api stackalpha-celery-worker stackalpha-celery-beat
```

### Test API
```bash
curl http://localhost:8000/health
curl https://api.stackalpha.xyz/health
```

---

## Troubleshooting

### API not starting
```bash
# Check logs
sudo journalctl -u stackalpha-api -n 50

# Check .env file exists
ls -la /home/stackalpha/stackalpha/.env

# Test manually
cd /home/stackalpha/stackalpha
source venv/bin/activate
python -c "from app.main import app; print('OK')"
```

### Database connection issues
```bash
# Check PostgreSQL is running
sudo systemctl status postgresql

# Test connection
psql -U stackalpha -d stackalpha -h localhost -c "SELECT 1;"
```

### Redis issues
```bash
# Check Redis is running
sudo systemctl status redis-server
redis-cli ping
```

### Nginx issues
```bash
# Test config
sudo nginx -t

# Check error log
sudo tail -f /var/log/nginx/error.log
```
