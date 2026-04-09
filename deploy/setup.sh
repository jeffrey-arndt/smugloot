#!/bin/bash
# SmugLoot bot deployment setup for Ubuntu/Debian (Digital Ocean droplet)
set -e

echo "=== SmugLoot Bot Setup ==="

# Install Python 3.12+ if needed
sudo apt update
sudo apt install -y python3 python3-venv python3-pip

# Create bot user (no login shell)
sudo useradd -r -s /bin/false smugloot 2>/dev/null || true

# Create app directory
sudo mkdir -p /opt/smugloot
sudo cp -r ../*.py ../requirements.txt ../cogs ../Juggs\ Loot\ Bias\ Spreadsheet*.csv /opt/smugloot/
sudo mkdir -p /opt/smugloot/cogs

# Set up venv
cd /opt/smugloot
sudo python3 -m venv venv
sudo ./venv/bin/pip install -r requirements.txt

# Prompt for .env if it doesn't exist
if [ ! -f /opt/smugloot/.env ]; then
    echo ""
    echo "Create /opt/smugloot/.env with your credentials:"
    echo "  sudo nano /opt/smugloot/.env"
    echo ""
    echo "Required variables:"
    echo "  WCL_CLIENT_ID=..."
    echo "  WCL_CLIENT_SECRET=..."
    echo "  DISCORD_BOT_TOKEN=..."
    echo "  DISCORD_GUILD_ID=..."
    echo "  PUBLISH_CHANNEL_ID=..."
fi

# Set ownership
sudo chown -R smugloot:smugloot /opt/smugloot

# Install systemd service
sudo cp smugloot.service /etc/systemd/system/smugloot.service 2>/dev/null || \
    sudo cp deploy/smugloot.service /etc/systemd/system/smugloot.service
sudo systemctl daemon-reload
sudo systemctl enable smugloot

echo ""
echo "=== Setup complete ==="
echo "1. Create /opt/smugloot/.env with your credentials"
echo "2. Start the bot:  sudo systemctl start smugloot"
echo "3. Check status:   sudo systemctl status smugloot"
echo "4. View logs:      sudo journalctl -u smugloot -f"
