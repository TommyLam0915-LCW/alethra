# Alethra Deployment Guide

Production deployment using Docker Compose with PostgreSQL, Redis, and Nginx.

---

## Prerequisites

| Requirement     | Minimum Version | Notes                                       |
|-----------------|-----------------|---------------------------------------------|
| Docker          | 24.0+           | [`docker --version`]                        |
| Docker Compose  | v2.20+          | [`docker compose version`] (plugin, not standalone) |
| Disk space      | 5 GB            | Images + data volumes                       |
| RAM             | 4 GB            | All services combined                       |
| Open ports      | 80, 443         | For Nginx; 5432/6379 bound to localhost only|

---

## Quick Start

```bash
# 1. Clone and enter the repository
git clone <repo-url> alethra && cd alethra

# 2. Create environment file
cp .env.example .env

# 3. Generate required secrets
openssl rand -hex 32   # use output for POSTGRES_PASSWORD, REDIS_PASSWORD, JWT_SECRET_KEY

# 4. Edit .env with real values (see Environment Variables below)

# 5. Start the stack
./deploy/up.sh

# 6. Verify
curl http://localhost/health
curl http://localhost/health/ready
```

---

## Environment Variables

All variables are documented in `.env.example`. **Required** variables (must be set before deployment):

| Variable             | Description                          | How to Generate                     |
|----------------------|--------------------------------------|-------------------------------------|
| `POSTGRES_PASSWORD`  | PostgreSQL superuser password        | `openssl rand -hex 32`              |
| `REDIS_PASSWORD`     | Redis AUTH password                  | `openssl rand -hex 32`              |
| `JWT_SECRET_KEY`     | HMAC signing key for JWT tokens      | `openssl rand -hex 32` (min 32 chars)|
| `CORS_ORIGINS`       | Allowed frontend origins             | `https://your-domain.com`           |
| `NEXT_PUBLIC_API_URL`| Public API URL for the frontend      | `https://your-domain.com`           |

**Optional** (have sensible defaults):

| Variable              | Default     | Notes                           |
|-----------------------|-------------|---------------------------------|
| `POSTGRES_USER`       | `alethra`   |                                 |
| `POSTGRES_DB`         | `alethra`   |                                 |
| `JWT_ALGORITHM`       | `HS256`     |                                 |
| `AI_PROVIDER`         | `mock`      | `mock` / `openai` / `anthropic` |
| `DATA_PROVIDER`       | `demo`      | `demo` / `lseg`                 |
| `APP_ENV`             | `production`| `development`/`staging`/`production` |

### Secret Management

- **Never** commit `.env` to version control.
- Rotate secrets periodically: change the value in `.env`, then `docker compose -f docker-compose.prod.yml up -d` to restart affected services.
- For stronger isolation, consider Docker secrets or a vault (HashiCorp Vault, AWS Secrets Manager) in place of `.env` files.

---

## Architecture

```
                 Port 80/443
                      |
                  +---v----+
                  |  Nginx  |
                  +---+----+
                      |
           +----------+----------+
           |                     |
      /api/*, /health      /* (everything else)
           |                     |
     +-----v------+      +------v-------+
     |  Backend    |      |  Frontend    |
     |  (FastAPI)  |      |  (Next.js)   |
     |  :8000      |      |  :3000       |
     +--+-------+--+      +--------------+
        |       |
   +----v--+ +--v----+
   |  PG   | | Redis  |
   | :5432 | | :6379  |
   +-------+ +--------+
```

---

## Deployment Scripts

| Script               | Purpose                                          |
|----------------------|--------------------------------------------------|
| `deploy/up.sh`       | Build images and start all services              |
| `deploy/down.sh`     | Stop services (pass `--volumes` to delete data)  |
| `deploy/migrate.sh`  | Run Alembic migrations against the live DB       |
| `deploy/backup.sh`   | Dump PostgreSQL to a timestamped `.sql.gz` file  |

### First-Time Deployment

```bash
cp .env.example .env
# ... edit .env ...
./deploy/up.sh
./deploy/migrate.sh
```

### Updating / Redeploying

```bash
git pull origin main
./deploy/up.sh            # rebuilds images, restarts services
./deploy/migrate.sh       # applies any new migrations
```

### Database Backup & Restore

```bash
# Backup
./deploy/backup.sh

# Restore (replace FILE with the backup path)
gunzip -c backups/alethra_db_XXXXXXXX_XXXXXX.sql.gz \
  | docker compose -f docker-compose.prod.yml exec -T db \
    psql -U alethra alethra
```

Backups are retained for 7 rotations (oldest pruned automatically).

---

## Health Checks

| Endpoint          | Purpose         | Success Response                |
|-------------------|-----------------|---------------------------------|
| `GET /health`     | Liveness probe  | `{"status": "healthy"}`         |
| `GET /health/ready`| Readiness probe| `{"status": "ready", ...}` (200)|

The readiness probe checks:
- **Database**: Executes `SELECT 1` against PostgreSQL. Critical -- returns 503 on failure.
- **Redis**: Sends `PING`. Non-critical -- `not_configured` is acceptable if `REDIS_URL` is empty.

### Example readiness response

```json
{
  "status": "ready",
  "checks": {
    "database": {"status": "connected", "latency_ms": 1.23},
    "redis": {"status": "connected", "latency_ms": 0.45}
  }
}
```

---

## HTTPS / TLS

The included Nginx config listens on port 80 (HTTP). To enable HTTPS:

1. Place certificate files in `deploy/nginx-ssl/`:
   - `fullchain.pem`
   - `privkey.pem`
2. Add an HTTPS `server` block to `deploy/nginx.conf` with `listen 443 ssl`.
3. Or, place a reverse proxy (Caddy, Traefik, or a cloud load balancer) in front of Nginx to handle TLS termination automatically.

---

## Troubleshooting

### Services fail to start

```bash
docker compose -f docker-compose.prod.yml logs backend     # check backend logs
docker compose -f docker-compose.prod.yml ps               # see which services are unhealthy
```

### Database connection refused

- Verify the `db` service is healthy: `docker compose -f docker-compose.prod.yml ps db`
- Check credentials in `.env` match `DATABASE_URL` construction.

### "JWT_SECRET_KEY must not be a well-known default"

- The application refuses to start in production mode with a weak JWT secret.
- Generate a new one: `openssl rand -hex 32`

### 503 from /health/ready

- Check the `checks` field in the response to identify which dependency is failing.
- Verify the database container is running and accepting connections.
- If Redis is intentionally unused, ensure `REDIS_URL` is empty (not an invalid URL).

### Frontend cannot reach backend

- Verify `NEXT_PUBLIC_API_URL` in `.env` matches the public URL of the API.
- This value is baked into the frontend bundle at build time; you must rebuild after changing it.

### Port conflicts

- PostgreSQL (5432) and Redis (6379) are bound to `127.0.0.1` only. If those ports are in use locally, stop the conflicting service or change the host mapping in `docker-compose.prod.yml`.

---

## Monitoring Recommendations

| Concern              | Recommendation                                                |
|----------------------|---------------------------------------------------------------|
| Container health     | Docker health checks are configured; integrate with monitoring tools (Datadog, Prometheus + cAdvisor) |
| Application logs     | `docker compose -f docker-compose.prod.yml logs -f` or ship to a log aggregator (Loki, CloudWatch) |
| Database             | Monitor `pg_stat_activity`, connection count, slow queries. Consider `pg_stat_exporter` for Prometheus |
| Redis                | Monitor memory usage, hit rate, connected clients via `redis-cli info` or `redis_exporter` |
| Uptime               | Ping `/health` and `/health/ready` from an external monitor (UptimeRobot, Healthchecks.io) |
| Backups              | Verify backup files are created daily and test restore quarterly |
| Resource usage       | Set memory limits in compose file (already configured); alert at 80% utilization |

---

## Security Checklist

- [ ] `.env` is not committed to git (listed in `.gitignore`)
- [ ] All passwords are generated with `openssl rand -hex 32` or stronger
- [ ] `JWT_SECRET_KEY` is at least 32 characters and unique per environment
- [ ] `CORS_ORIGINS` specifies explicit domains (no wildcards)
- [ ] PostgreSQL and Redis ports are bound to `127.0.0.1` (not `0.0.0.0`)
- [ ] TLS is enabled for external traffic (HTTPS on port 443)
- [ ] `APP_ENV` is set to `production`
- [ ] `ENABLE_LIVE_PAYMENTS` is `false` until Stripe keys are configured and tested
- [ ] Docker images are rebuilt from tagged releases, not `latest`
