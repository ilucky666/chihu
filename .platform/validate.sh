#!/usr/bin/env bash
# PR validation only: all resources below contain synthetic data and are removed on exit.
set -Eeuo pipefail
cd "$(dirname "$0")/.."
image="${CHIHU_TEST_IMAGE:-}"
if [[ -z "$image" ]]; then
  image=$(python3 -c 'import json,os; print(json.loads(os.environ["IMAGE_REFS"])["default"])')
fi
suffix="${GITHUB_RUN_ID:-local}-$$"
network="chihu-test-${suffix}"
database="chihu-test-db-${suffix}"
app="chihu-test-app-${suffix}"
volume="chihu-test-media-${suffix}"
cleanup() {
  docker rm -f "$app" "$database" >/dev/null 2>&1 || true
  docker volume rm "$volume" >/dev/null 2>&1 || true
  docker network rm "$network" >/dev/null 2>&1 || true
}
trap cleanup EXIT
docker image inspect "$image" >/dev/null
docker network create "$network" >/dev/null
docker volume create "$volume" >/dev/null
docker run -d --name "$database" --network "$network" \
  -e MYSQL_ROOT_PASSWORD=test-only-password -e MYSQL_DATABASE=chihu_test \
  "${CHIHU_TEST_MYSQL_IMAGE:-mysql:8.4}" \
  --character-set-server=utf8mb4 --collation-server=utf8mb4_bin >/dev/null
ready=false
for ((i=0; i<90; i++)); do
  if docker exec "$database" mysqladmin ping -h 127.0.0.1 -ptest-only-password 2>/dev/null | grep -q alive; then
    ready=true; break
  fi
  sleep 2
done
[[ "$ready" == true ]] || { echo 'Synthetic MySQL did not start' >&2; exit 1; }
# Give the non-root application user ownership of this disposable test volume.
docker run --rm --user root --entrypoint chown -v "$volume:/app/media" "$image" \
  10001:10001 /app/media
start_app() {
  docker run -d --name "$app" --network "$network" -v "$volume:/app/media" \
    --memory 512m --memory-swap 512m --cpus 1.5 \
    -e DATABASE_URL="mysql://root:test-only-password@${database}:3306/chihu_test" \
    -e DJANGO_SECRET_KEY=test-only-long-secret-key-for-disposable-platform-validation \
    -e DJANGO_ALLOWED_HOSTS=127.0.0.1 -e DJANGO_CSRF_TRUSTED_ORIGINS=https://127.0.0.1 \
    -e EATFUL_MEDIA_STORAGE=filesystem -e EATFUL_MEDIA_PERSISTENT=1 "$image" >/dev/null
  local healthy=false
  # Match the current default deploy-agent's 15 x 2-second readiness window.
  for ((i=0; i<15; i++)); do
    sleep 2
    if [[ "$(docker inspect --format '{{.State.Health.Status}}' "$app")" == healthy ]]; then
      healthy=true; break
    fi
    if [[ "$(docker inspect --format '{{.State.Status}}' "$app")" == exited ]]; then
      break
    fi
  done
  [[ "$healthy" == true ]] || { docker logs "$app"; exit 1; }
}
start_app
docker exec "$app" python manage.py shell -c \
  "exec(open('scripts/smoke_deployment.py').read()); seed()"
docker rm -f "$app" >/dev/null
start_app
docker exec "$app" python manage.py shell -c \
  "exec(open('scripts/smoke_deployment.py').read()); verify()"
# Killing a supervised background process must fail the container, not leave a healthy web-only app.
docker exec "$app" python -c '
import os,signal
from pathlib import Path
for p in Path("/proc").iterdir():
    if p.name.isdigit():
        try: args=(p/"cmdline").read_bytes().split(b"\0")
        except (FileNotFoundError,PermissionError): continue
        if b"manage.py" in args and b"run_worker" in args: os.kill(int(p.name),signal.SIGTERM)
'
for ((i=0; i<30; i++)); do
  if [[ "$(docker inspect --format '{{.State.Status}}' "$app")" == exited ]]; then
    echo 'Platform smoke passed: migrations, private files, replacement persistence and supervision'
    exit 0
  fi
  sleep 1
done
echo 'Container did not stop after its worker exited' >&2
exit 1
