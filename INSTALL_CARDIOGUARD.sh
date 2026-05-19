#!/bin/bash
echo "=== Installation CardioGuard ==="
source /home/azureuser/venv/bin/activate
pip install flask-login -q
mkdir -p /home/azureuser/app/templates
cp app.py      /home/azureuser/app/
cp database.py /home/azureuser/app/
cp templates/* /home/azureuser/app/templates/
cd /home/azureuser/app
python3 database.py
sudo tee /etc/systemd/system/ecg-dashboard.service > /dev/null << SVC
[Unit]
Description=CardioGuard — Surveillance Cardiaque
After=network.target mosquitto.service
[Service]
User=root
WorkingDirectory=/home/azureuser/app
Environment=PATH=/home/azureuser/venv/bin
ExecStart=/home/azureuser/venv/bin/python3 app.py
Restart=always
RestartSec=5
[Install]
WantedBy=multi-user.target
SVC
sudo systemctl daemon-reload
sudo systemctl restart ecg-dashboard
echo ""
echo "================================================"
echo "  CardioGuard installé !"
echo "  URL     : http://74.161.34.200"
echo "  Médecin : medecin@ecg.tn / medecin123"
echo "  Code    : ISSRA2026"
echo "================================================"
