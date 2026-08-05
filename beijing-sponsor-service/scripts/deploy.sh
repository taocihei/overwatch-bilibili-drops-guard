#!/usr/bin/env bash
set -Eeuo pipefail

if [[ ${EUID} -ne 0 ]]; then
  echo "Run as root: sudo ./scripts/deploy.sh" >&2
  exit 1
fi

SOURCE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APP_ROOT="/opt/beijing-sponsor-service"
RELEASE_ID="$(date -u +%Y%m%d%H%M%S)"
RELEASE_DIR="${APP_ROOT}/releases/${RELEASE_ID}"
CURRENT_LINK="${APP_ROOT}/current"
PREVIOUS_FILE="${APP_ROOT}/previous-release"
SERVICE_USER="sponsor-service"

id -u "${SERVICE_USER}" >/dev/null 2>&1 || \
  useradd --system --home-dir /nonexistent --shell /usr/sbin/nologin "${SERVICE_USER}"
install -d -m 0755 "${APP_ROOT}/releases"
install -d -o "${SERVICE_USER}" -g "${SERVICE_USER}" -m 0750 /var/lib/beijing-sponsor-service

if [[ ! -f /etc/sponsor-service.env ]]; then
  install -m 0600 "${SOURCE_DIR}/.env.example" /etc/sponsor-service.env
  echo "Created /etc/sponsor-service.env. Replace the example values, then rerun deploy." >&2
  exit 2
fi
if grep -Eq '(^|=)replace_(me|with_)' /etc/sponsor-service.env; then
  echo "/etc/sponsor-service.env still contains example values." >&2
  exit 2
fi

install -d -m 0755 "${RELEASE_DIR}"
rsync -a --delete \
  --exclude '.git' --exclude '.venv' --exclude '__pycache__' \
  --exclude 'data' --exclude '*.sqlite3*' \
  "${SOURCE_DIR}/" "${RELEASE_DIR}/"

python3 -m venv "${RELEASE_DIR}/.venv"
"${RELEASE_DIR}/.venv/bin/pip" install --disable-pip-version-check --no-cache-dir \
  -r "${RELEASE_DIR}/requirements.txt"
(
  cd "${RELEASE_DIR}"
  "${RELEASE_DIR}/.venv/bin/python" -m unittest discover -s tests -v
)

if [[ -L "${CURRENT_LINK}" ]]; then
  readlink -f "${CURRENT_LINK}" > "${PREVIOUS_FILE}"
fi
ln -sfn "${RELEASE_DIR}" "${CURRENT_LINK}.new"
mv -Tf "${CURRENT_LINK}.new" "${CURRENT_LINK}"
chown -R root:root "${RELEASE_DIR}"

install -m 0644 "${RELEASE_DIR}/deploy/sponsor-service.service" \
  /etc/systemd/system/sponsor-service.service
systemctl daemon-reload
systemctl enable sponsor-service.service

if ! systemctl restart sponsor-service.service || \
   ! curl --fail --silent --show-error --retry 12 --retry-delay 1 --retry-connrefused \
      http://127.0.0.1:8765/api/health >/dev/null; then
  if [[ -s "${PREVIOUS_FILE}" ]]; then
    echo "Deployment health check failed; rolling back." >&2
    bash "${RELEASE_DIR}/scripts/rollback.sh"
  else
    echo "Initial deployment health check failed; no previous release exists." >&2
    systemctl stop sponsor-service.service || true
  fi
  exit 1
fi

echo "Deployed ${RELEASE_ID}."
echo "Install deploy/nginx-sponsor.conf after replacing api.example.com, then run nginx -t."
