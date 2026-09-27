# Shanhai Wander setup

## Enable AMap

Create an application at https://lbs.amap.com/ and request two credential types:

- **Web Service** key: place, hotel, charging-station and road queries.
- **Web JS API** key and its security code: the map displayed in the browser.

Set `AMAP_WEB_KEY`, `AMAP_JS_KEY` and `AMAP_JS_SECURITY_CODE` in `.env`. Restrict the JS key to your domain; configure your service permissions, quota and server IP restrictions in AMap's console. Only the JS key reaches the browser. The service key and JS security code remain on the server.

After editing `.env`, recreate the app:

```bash
docker compose up -d --force-recreate app
```

For production, include both Compose files in this command. Restarting a container alone does not reload its environment variables.

If search fails, check the credential type, quota and IP restrictions. If the map stays a sketch, check the JS key, security code, domain restrictions and browser network access. AMap's own terms and quotas apply.

## Deploy on a server

1. Install Docker and Compose 2.24.4 or later.
2. Point a domain at the server and allow incoming traffic on ports 80 and 443.
3. Set `DOMAIN=your.real.domain` in `.env`.
4. Run:

```bash
docker compose -f compose.yaml -f compose.production.yaml up -d --build
```

Caddy provisions HTTPS. Production settings disable debug mode and enable secure cookies. The application port stays internal. Open your domain and create an account.

Create an administrator interactively, then sign in at `/admin/`:

```bash
docker compose -f compose.yaml -f compose.production.yaml exec app python manage.py createsuperuser
```

There is no default admin or password. If an account holder forgets their password, a trusted administrator can run `python manage.py changepassword USERNAME` inside the app container. Email recovery is not configured.

The persistent data volume is `shanhai_wander_data`. The app generates its own secret key in that volume on first launch. Keep the volume when upgrading. The app and its SQLite database are designed for a single server; heavy concurrent writes or multiple servers need a database architecture change.

## Back up your trips

```bash
docker compose exec -T app python scripts/backup.py
```

The command prints an archive path under `/app/data/backups/`. It includes a consistent SQLite snapshot and the persistent application secret. Copy the archive off the server, substituting the actual filename:

```bash
docker compose cp app:/app/data/backups/ACTUAL-FILENAME.tar.gz ./
```

Backups contain private account and trip data. Protect them. Save `.env` separately if it contains a custom `SECRET_KEY` or AMap credentials.

To restore, stop the app first. Put the archive in a local `restore-input` directory, and run:

```bash
docker compose stop app
docker compose run --rm -v ./restore-input:/restore-input:ro app python scripts/restore.py /restore-input/ACTUAL-FILENAME.tar.gz --yes
docker compose up -d
```

The restore utility validates the archive and saves a backup of the previous database before replacing it. For a production deployment, pass `-f compose.yaml -f compose.production.yaml` to every Compose command above. Prefer restoring with the code version that produced the backup before upgrading.

## Update

Back up first, then:

```bash
git pull --ff-only
docker compose -f compose.yaml -f compose.production.yaml up -d --build
```

Startup applies database migrations and updates built-in templates without changing user itineraries. See logs with `docker compose logs --tail=80 app`. Never use `docker compose down -v` unless you intend to erase your data.

## Local Python development

On Linux, macOS or WSL2 with Python 3.12+, run `./scripts/bootstrap.sh`. The script prepares the environment and starts a local development server. Native Windows users should use Docker or WSL2.

Runtime dependencies are pinned in `requirements.lock`. The optional browser tests need Node.js 22+:

```bash
python manage.py test
npm ci
npx playwright install --with-deps chromium
npm run test:e2e
```

Run browser tests only against a test instance: they create demonstration accounts and trips.
