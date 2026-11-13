#!/usr/bin/env sh
# Update the site on the droplet: pull the latest code, rebuild the image, restart, and check /healthz.
# Run on the droplet, in the repository folder:   sh deploy/deploy.sh
set -eu
cd "$(dirname "$0")/.."
git pull --ff-only origin main
docker compose up -d --build --remove-orphans      # --remove-orphans: the old 24-hour `backup` container is gone (the app backs up itself now)
echo "waiting for the server..."
for i in 1 2 3 4 5 6 7 8 9 10 11 12; do
  if docker compose exec -T app python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=3).status==200 else 1)" 2>/dev/null; then
    echo "OK - the site is up"; docker image prune -f >/dev/null; exit 0
  fi
  sleep 5
done
echo "The server did not answer /healthz - check: docker compose logs app" >&2
exit 1
