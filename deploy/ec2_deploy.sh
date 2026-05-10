#!/bin/bash
# ============================================================
# CabService EC2 Deployment Script (Ubuntu 22.04)
# Run this on your EC2 instance as ubuntu user
# ============================================================
set -e

echo "===== Step 1: System Update ====="
sudo apt update && sudo apt upgrade -y

echo "===== Step 2: Install Dependencies ====="
sudo apt install -y python3 python3-pip python3-venv \
    postgresql postgresql-contrib \
    nginx git curl ufw

echo "===== Step 3: Clone Repository ====="
cd /home/ubuntu
git clone https://github.com/YOUR_USERNAME/cabservice.git
cd cabservice

echo "===== Step 4: Python Virtual Environment ====="
python3 -m venv venv
source venv/bin/activate

echo "===== Step 5: Install Python Packages ====="
pip install --upgrade pip
pip install -r backend/requirements.txt

echo "===== Step 6: Setup Environment Variables ====="
# Copy and edit your .env file
cp .env.example .env
echo ">>> EDIT /home/ubuntu/cabservice/.env with your values <<<"
echo ">>> Press Enter after editing to continue..."
read

echo "===== Step 7: Django Setup ====="
cd backend
source /home/ubuntu/cabservice/venv/bin/activate
export $(cat /home/ubuntu/cabservice/.env | grep -v '#' | xargs)

python manage.py migrate
python manage.py collectstatic --noinput
python manage.py seed_data

echo "===== Step 8: Gunicorn Log Directory ====="
sudo mkdir -p /var/log/gunicorn
sudo chown ubuntu:www-data /var/log/gunicorn

echo "===== Step 9: Gunicorn Service ====="
sudo cp /home/ubuntu/cabservice/deploy/gunicorn/cabservice.service \
    /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable cabservice
sudo systemctl start cabservice

echo "===== Step 10: Nginx Config ====="
sudo cp /home/ubuntu/cabservice/deploy/nginx/cabservice.conf \
    /etc/nginx/sites-available/cabservice
sudo ln -sf /etc/nginx/sites-available/cabservice \
    /etc/nginx/sites-enabled/
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t
sudo systemctl restart nginx

echo "===== Step 11: Firewall ====="
sudo ufw allow 'Nginx Full'
sudo ufw allow OpenSSH
sudo ufw --force enable

echo "===== Deployment Complete! ====="
echo "Backend running at: http://$(curl -s ifconfig.me)"
echo "Check status: sudo systemctl status cabservice"
echo "Check logs:   sudo journalctl -u cabservice -f"
