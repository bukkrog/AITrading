# DEPLOY — Ubuntu 22.04 LTS (Proxmox VM)

Kør platformen som **én systemd-service på port 8000** (API + web-UI fra samme
proces — ingen nginx eller node-proces nødvendig på serveren).

---

## 0. Sluk den gamle webserver først

Find hvad der lytter, og hvilken service det er:

```bash
sudo ss -tlnp | grep -E ':80 |:443 |:8000 |:3000 |:5173 '
systemctl list-units --type=service --state=running | grep -Ei 'nginx|apache|caddy|httpd|lighttpd|node|pm2'
```

Sluk + deaktivér den (typisk én af disse):

```bash
sudo systemctl disable --now nginx        # eller:
sudo systemctl disable --now apache2
sudo systemctl disable --now caddy
# pm2-styret node-app:  pm2 stop all && pm2 delete all && pm2 unstartup
```

`disable --now` = stop nu **og** start ikke igen ved boot. Verificér med
`sudo ss -tlnp | grep ':80 '` (skal være tom).

## 1. Forudsætninger (Ubuntu 22.04 har Python 3.10 — vi skal bruge 3.12)

```bash
sudo apt update
sudo apt install -y git curl software-properties-common
sudo add-apt-repository -y ppa:deadsnakes/ppa
sudo apt install -y python3.12 python3.12-venv

# Node 20 (kun til at BYGGE frontend — kører ikke som service)
curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash -
sudo apt install -y nodejs
```

## 2. Hent og byg

```bash
sudo mkdir -p /opt && cd /opt
sudo git clone https://github.com/bukkrog/AITrading.git
sudo chown -R $USER:$USER /opt/AITrading
cd /opt/AITrading

python3.12 -m venv .venv
.venv/bin/pip install -r requirements.txt

cd frontend && npm ci && npm run build && cd ..
```

## 3. Opret `.env`

Kopiér skabelonen fra `ONBOARDING.md` til `/opt/AITrading/.env`. **Vigtigt på en
server** — tilføj disse to (API'et er nu tilgængeligt på netværket):

```ini
API_KEY=vaelg-en-lang-tilfaeldig-noegle
# Valgfrit: kritiske alerts til Slack/Discord/Teams/ntfy
# ALERT_WEBHOOK_URL=https://...
# Valgfrit: persistér Saxo-token på tværs af genstarter
# SAXO_ACCESS_TOKEN=...

# Anbefalet: Saxo OAuth (DEMO-app fra developer.saxo) — så slipper du for 24h-tokens.
# Registrér Redirect URL'en på appen under developer.saxo -> Application Management!
SAXO_APP_KEY=din-app-key
SAXO_APP_SECRET=din-app-secret
SAXO_REDIRECT_URI=http://<VM-IP>:8000/control/saxo/callback
```

**Saxo OAuth (engangs-login i stedet for dagligt token):** udfyld de tre
SAXO_*-felter ovenfor (eller i Setup-siden), genstart, og klik **"Log ind hos
Saxo (OAuth)"** i Setup → log ind → sessionen fornyes herefter automatisk og
overlever genstarter (refresh-token gemmes i `saxo_oauth.json`, git-ignored).

I browseren (én gang, F12 → Console) så UI'et sender nøglen:
`localStorage.setItem("aitp_api_key", "vaelg-en-lang-tilfaeldig-noegle")`

## 4. Systemd-service

```bash
sudo tee /etc/systemd/system/aitrading.service > /dev/null <<'EOF'
[Unit]
Description=AI Trading Platform
After=network-online.target
Wants=network-online.target

[Service]
User=REPLACE_ME_USER
WorkingDirectory=/opt/AITrading
ExecStart=/opt/AITrading/.venv/bin/python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

sudo sed -i "s/REPLACE_ME_USER/$USER/" /etc/systemd/system/aitrading.service
sudo systemctl daemon-reload
sudo systemctl enable --now aitrading
```

## 5. Firewall + verificér

```bash
sudo ufw allow 8000/tcp        # hvis ufw er aktiv
systemctl status aitrading --no-pager
curl -s http://localhost:8000/health
```

Åbn **http://\<VM-IP\>:8000** — hele UI'et serveres derfra (samme port som API'et).

## 6. Drift

```bash
journalctl -u aitrading -f            # live-log
sudo systemctl restart aitrading      # genstart (fx efter git pull)
cd /opt/AITrading && git pull && cd frontend && npm run build && cd .. \
  && sudo systemctl restart aitrading   # opdatér til nyeste version
```

**Noter:**
- Automation-loopet genoptager selv efter genstart (hvis det var startet).
- Saxo 24h-tokenet skal stadig fornys dagligt (Setup-siden, eller `.env`).
- Databasen er `/opt/AITrading/trading.db` (SQLite) — tag backup af den fil.
- Markeds-tider bruger IANA-tidszoner — virker out-of-the-box på Linux.
