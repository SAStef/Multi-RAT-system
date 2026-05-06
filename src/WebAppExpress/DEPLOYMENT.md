# 24/7 Deployment

Dashboardet kan koere 24/7 paa cloud-serveren, hvis Node-processen holdes i live af en process manager.

## Porte

- `3000/tcp` - web dashboard
- `6967/udp` - UDP receiver path 1
- `6968/udp` - UDP receiver path 2

## Start manuelt

```bash
cd src/WebAppExpress
npm start
```

Dashboard: `http://SERVER_IP:3000`

Health check: `http://SERVER_IP:3000/health`

## PM2

```bash
cd src/WebAppExpress
pm2 start server.js --name multirat-dashboard
pm2 save
pm2 startup
```

Useful checks:

```bash
pm2 status
pm2 logs multirat-dashboard
```

## systemd

Create `/etc/systemd/system/multirat-dashboard.service` on the server and adjust `WorkingDirectory` to the real repo path:

```ini
[Unit]
Description=Multi-RAT Dashboard
After=network.target

[Service]
Type=simple
WorkingDirectory=/opt/Multi-RAT-system/src/WebAppExpress
ExecStart=/usr/bin/node server.js
Restart=always
RestartSec=3
Environment=NODE_ENV=production
Environment=PORT=3000

[Install]
WantedBy=multi-user.target
```

Enable it:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now multirat-dashboard
sudo systemctl status multirat-dashboard
```
