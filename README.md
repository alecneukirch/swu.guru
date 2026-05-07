# swu.guru

Meta analytics site for Star Wars Unlimited — tracks tournament results, leader win rates, matchup data, and decklists scraped from Melee.gg. Live at [Swu.Guru](https://swu.guru/).

## Stack

| Service | Role |
|---|---|
| **FastAPI** | REST API + serves the single-page frontend |
| **PostgreSQL** | Primary database (external, not containerized) |
| **Traefik v3** | Reverse proxy, TLS termination (Let's Encrypt via Cloudflare DNS), rate limiting |
| **Prometheus** | Metrics collection |
| **Grafana** | Dashboards (cAdvisor, node-exporter, Traefik) |
| **cAdvisor** | Per-container resource metrics |
| **node-exporter** | Host-level metrics |
| **Falco** | Runtime security monitoring |

## Setup

### 1. Configure environment

```bash
cp .env.example .env
# Edit .env — fill in your domain, Cloudflare token, DB credentials
```

Required `.env` values:

```
DOMAIN=yourdomain.com
ACME_EMAIL=your@email.com
CF_DNS_API_TOKEN=           # Cloudflare token with Zone:DNS:Edit
DB_HOST=                    # External Postgres host
DB_PORT=5432
DB_NAME=
DB_USER=
DB_PASS=
GRAFANA_USER=admin
GRAFANA_PASSWORD=
```

### 2. Traefik basic auth (for dashboard + Prometheus)

Generate a bcrypt hash for your admin password and write it to `traefik/dynamic/.htpasswd`:

```bash
# Install apache2-utils if needed: apt install apache2-utils
htpasswd -nbB admin 'yourpassword' > traefik/dynamic/.htpasswd
```

### 3. Start the stack

```bash
docker compose up -d
docker compose logs -f app
```

Services come up on:
- `https://yourdomain.com` — main app
- `https://traefik.yourdomain.com` — Traefik dashboard (basic auth)
- `https://grafana.yourdomain.com` — Grafana
- `https://prometheus.yourdomain.com` — Prometheus (basic auth + IP allowlist)

## Data Scrapers

Scrapers live in `scraper/` and pull tournament data from Melee.gg.

```bash
# Sync card data
docker exec swuguru python -m scraper.melee --cards

# Sync Eternal format cards
docker exec swuguru python -m scraper.melee --cards --eternal

# Full tournament scrape
docker exec swuguru python -m scraper.melee
```

The `--cards` jobs run daily via cron. See `crontab -l` on the host.

## Project Structure

```
.
├── api/                  # FastAPI application
├── scraper/              # Melee.gg data scrapers
├── frontend/             # Single-file SPA (index.html)
├── traefik/
│   ├── traefik.yml       # Static config
│   └── dynamic/          # Hot-reloaded middlewares, TLS options
├── prometheus/           # Scrape config + alert rules
├── grafana/
│   ├── provisioning/     # Auto-provisioned datasources + dashboards
│   └── dashboards/       # Dashboard JSON files
├── falco/                # Runtime security rules
├── docker-compose.yml
├── Dockerfile
└── .env.example
```

## Networks

Three isolated Docker networks:

- `dmz` — public-facing (Traefik, app)
- `internal` — backend only, no internet gateway
- `metrics` — isolated metrics traffic (Prometheus, Grafana, cAdvisor, Falco)

## TLS

Certificates are obtained automatically via Let's Encrypt DNS-01 challenge through Cloudflare. This works behind NAT and Cloudflare proxy. Certs are stored in the `traefik_certs` Docker volume.
