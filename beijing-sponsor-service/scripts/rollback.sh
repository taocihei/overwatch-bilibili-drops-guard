#!/usr/bin/env bash
set -Eeuo pipefail

if [[ ${EUID} -ne 0 ]]; then
  echo "Run as root: sudo ./scripts/rollback.sh" >&2
  exit 1
fi

APP_ROOT="/opt/beijing-sponsor-service"
CURRENT_LINK="${APP_ROOT}/current"
PREVIOUS_FILE="${APP_ROOT}/previous-release"

if [[ ! -s "${PREVIOUS_FILE}" ]]; then
  echo "No previous release is recorded." >&2
  exit 1
fi
PREVIOUS="$(cat "${PREVIOUS_FILE}")"
case "${PREVIOUS}" in
  "${APP_ROOT}"/releases/*) ;;
  *) echo "Recorded rollback path is invalid." >&2; exit 1 ;;
esac
if [[ ! -d "${PREVIOUS}" ]]; then
  echo "Previous release no longer exists: ${PREVIOUS}" >&2
  exit 1
fi

CURRENT="$(readlink -f "${CURRENT_LINK}" || true)"
ln -sfn "${PREVIOUS}" "${CURRENT_LINK}.new"
mv -Tf "${CURRENT_LINK}.new" "${CURRENT_LINK}"
if [[ -n "${CURRENT}" ]]; then
  printf '%s\n' "${CURRENT}" > "${PREVIOUS_FILE}"
fi
systemctl restart sponsor-service.service
curl --fail --silent --show-error --retry 12 --retry-delay 1 --retry-connrefused \
  http://127.0.0.1:8765/api/health >/dev/null
echo "Rolled back to ${PREVIOUS}."
