# MindDrop

MindDrop is a self-hosted MindChuk-style capture inbox for a small team. It gives you:

- accounts for you and coworkers
- private or team-visible chunks
- quick capture for notes, ideas, links, tasks, and meeting notes
- automatic title, summary, and tags through an optional local LLM
- SQLite full-text search
- an API intake endpoint for n8n, Telegram, email, SMS, shortcuts, or webhooks

## What MindChuk Appears To Be

From the public site, MindChuk is a private feed for your brain: text or save anything, then keep it searchable, tagged, and organized. The public app uses React, Supabase auth/database, Stripe checkout, user profiles, phone fields, and a dashboard.

This project recreates the useful self-hosted core for Proxmox. It does not clone their billing, branding, or proprietary backend.

## Proxmox Setup

Recommended layout:

1. Create a small Debian or Ubuntu VM in Proxmox.
2. Give it 2 vCPU, 2-4 GB RAM, and 20+ GB disk.
3. Install Docker and Compose.
4. Copy this `mindchuk-local` folder onto the VM.
5. Create your environment file:

```bash
cp .env.example .env
nano .env
```

Set at least:

```bash
APP_SECRET=a-long-random-secret
INVITE_CODE=the-code-you-will-give-coworkers
INTAKE_TOKEN=a-long-random-webhook-token
```

Start it:

```bash
docker compose up -d --build
```

Open:

```text
http://YOUR_VM_IP:8080
```

The first account you create becomes the admin account.

### Debian/Ubuntu Docker Bootstrap

Run this inside the Proxmox VM if Docker is not installed yet:

```bash
sudo apt update
sudo apt install -y ca-certificates curl
sudo install -m 0755 -d /etc/apt/keyrings
. /etc/os-release
sudo curl -fsSL "https://download.docker.com/linux/$ID/gpg" -o /etc/apt/keyrings/docker.asc
sudo chmod a+r /etc/apt/keyrings/docker.asc
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/$ID $VERSION_CODENAME stable" | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null
sudo apt update
sudo apt install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
sudo usermod -aG docker "$USER"
```

Log out and back in after adding your user to the `docker` group.

## Local LLM Connection

If your LLM exposes an OpenAI-compatible API, put it in `.env`:

```bash
LLM_API_BASE=http://host.docker.internal:11434/v1
LLM_API_KEY=local
LLM_MODEL=llama3.1
```

For Ollama, make sure the model exists:

```bash
ollama pull llama3.1
```

If `LLM_API_BASE` is blank or unreachable, MindDrop still works and uses simple built-in tagging.

## Intake API

Use this from n8n, Apple Shortcuts, a Telegram bot, email parser, or SMS gateway:

```bash
curl -X POST http://YOUR_VM_IP:8080/api/intake \
  -H "Authorization: Bearer YOUR_INTAKE_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "user_email": "you@example.com",
    "body": "Follow up with Sam about the camera quote",
    "source_type": "shortcut",
    "visibility": "private",
    "tags": ["follow-up"]
  }'
```

## Backups

The database lives in the Docker volume `minddrop_data`. Back up the volume or copy `/data/minddrop.sqlite3` from inside the container.

For Proxmox, also enable scheduled VM backups. That gives you both app-level and server-level recovery.

## Next Useful Additions

- delete/edit chunks
- file uploads and OCR
- Telegram or SMS bot
- team spaces instead of one shared team feed
- daily digest email from recent chunks
- reverse proxy with HTTPS through Nginx Proxy Manager, Caddy, or Cloudflare Tunnel
