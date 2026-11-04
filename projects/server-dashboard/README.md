# Server Dashboard

Local dashboard for this Windows computer, a Raspberry Pi over SSH, and the official RSI Star Citizen status page.

## Run

```powershell
cd C:\Users\baylo\Documents\Codex\2026-06-02\i-am-ssh-into-my-pi\outputs\server-dashboard
npm start
```

Open:

```text
http://localhost:4177
```

## Configuration

The Pi target defaults to:

```text
dashpi@127.0.0.1
```

To use a different SSH target:

```powershell
$env:DASHBOARD_PI_HOST="user@server-address"
npm start
```

The ADS-B Pi Zero target defaults to:

```text
pi@127.0.0.1
```

To use a different ADS-B Pi target:

```powershell
$env:DASHBOARD_ADSB_HOST="user@adsb-pi-address"
npm start
```

The ADS-B aircraft feed defaults to:

```text
http://127.0.0.1/tar1090/data/aircraft.json
```

To use a different tar1090/readsb/dump1090 aircraft feed:

```powershell
$env:DASHBOARD_ADSB_AIRCRAFT_URL="http://adsb-pi/tar1090/data/aircraft.json"
npm start
```

The dashboard includes an ADS-B Sky Camera view. It reads the aircraft feed, stores recent positions in memory, and replays trails for the last 5, 15, or 30 minutes.

For the most camera-like view, set the receiver location before starting the dashboard:

```powershell
$env:DASHBOARD_ADSB_RECEIVER_LAT="your-latitude"
$env:DASHBOARD_ADSB_RECEIVER_LON="your-longitude"
npm start
```

Without receiver coordinates, the Sky Camera still works as an overhead aircraft track view based on the positions reported by tar1090/readsb/dump1090.

To use a different port:

```powershell
$env:DASHBOARD_PORT="4180"
npm start
```

To use a different Grafana dashboard:

```powershell
$env:DASHBOARD_GRAFANA_URL="http://server:3000/d/your-dashboard"
npm start
```

The bundled Grafana link is:

```text
http://127.0.0.1:3000/d/homelab-hardware/homelab-hardware-monitor?orgId=1&from=now-1h&to=now&timezone=browser&refresh=10s
```

Grafana currently sends `X-Frame-Options: deny`, so this dashboard opens Grafana in a separate tab instead of embedding it.

## Pi SSH Access

The dashboard runs SSH non-interactively, so password-only SSH will show `Needs SSH`.

Create a key if needed:

```powershell
ssh-keygen -t ed25519
```

Copy the public key to the Pi:

```powershell
type $env:USERPROFILE\.ssh\id_ed25519.pub | ssh dashpi@127.0.0.1 "mkdir -p ~/.ssh && cat >> ~/.ssh/authorized_keys && chmod 700 ~/.ssh && chmod 600 ~/.ssh/authorized_keys"
```

Then verify:

```powershell
ssh -o BatchMode=yes dashpi@127.0.0.1 hostname
```

If that command prints the Pi hostname, refresh the dashboard and the Pi card should populate.

## Add To Existing Grafana Dashboard

This dashboard also exposes Prometheus metrics:

```text
http://127.0.0.1:4177/metrics
```

Add this scrape target to Prometheus on the Grafana server:

```yaml
scrape_configs:
  - job_name: codex-dashboard
    scrape_interval: 15s
    metrics_path: /metrics
    static_configs:
      - targets:
          - 127.0.0.1:4177
```

The same snippet is saved as:

```text
prometheus-scrape-codex-dashboard.yml
```

After Prometheus is scraping the endpoint, append panels to your existing Grafana dashboard:

```powershell
$env:GRAFANA_TOKEN="your-grafana-api-token"
.\add-to-existing-grafana.ps1
```

Or use username/password:

```powershell
$env:GRAFANA_USER="admin"
$env:GRAFANA_PASSWORD="your-password"
.\add-to-existing-grafana.ps1
```

The script updates dashboard UID `homelab-hardware` at `http://127.0.0.1:3000`.
