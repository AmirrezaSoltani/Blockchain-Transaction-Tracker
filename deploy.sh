#!/usr/bin/env bash
set -euo pipefail

# Deploy BSC alert bot to a remote Docker host.
# Usage:
#   ./deploy.sh
#   ./deploy.sh --user liana --host 192.168.178.37
#
# Optional env overrides:
#   REMOTE_HOST, REMOTE_USER, REMOTE_DIR, SSH_PORT

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REMOTE_HOST="${REMOTE_HOST:-192.168.178.37}"
REMOTE_USER="${REMOTE_USER:-liana}"
# Empty means: use $HOME/bsc-alert-bot on the remote host (writable by non-root).
REMOTE_DIR="${REMOTE_DIR:-}"
SSH_PORT="${SSH_PORT:-22}"

usage() {
  cat <<EOF
Usage: $0 [options]

Prompts for the SSH password interactively.

Options:
  --host <ip>       Remote host (default: 192.168.178.37)
  --user <user>     SSH user (default: liana)
  --dir <path>      Remote deploy directory (default: ~/bsc-alert-bot)
  --port <port>     SSH port (default: 22)
  -h, --help        Show this help

Examples:
  $0
  $0 --user ubuntu --host 192.168.178.37
  $0 --dir /opt/bsc-alert-bot   # needs root write access
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help)
      usage
      exit 0
      ;;
    --host)
      REMOTE_HOST="$2"
      shift 2
      ;;
    --user)
      REMOTE_USER="$2"
      shift 2
      ;;
    --dir)
      REMOTE_DIR="$2"
      shift 2
      ;;
    --port)
      SSH_PORT="$2"
      shift 2
      ;;
    -*)
      echo "Unknown option: $1" >&2
      usage
      exit 1
      ;;
    *)
      echo "Unexpected argument: $1" >&2
      echo "Password is prompted interactively; do not pass it on the command line." >&2
      usage
      exit 1
      ;;
  esac
done

echo "Deploy target: ${REMOTE_USER}@${REMOTE_HOST}:${SSH_PORT}"
read -r -s -p "SSH password: " PASSWORD
echo
if [[ -z "$PASSWORD" ]]; then
  echo "Error: password cannot be empty." >&2
  exit 1
fi

if ! command -v sshpass >/dev/null 2>&1; then
  echo "Error: sshpass is required. Install with: sudo apt install sshpass" >&2
  exit 1
fi

if [[ ! -f "$SCRIPT_DIR/.env" ]]; then
  echo "Error: .env file not found."
  echo "Copy .env.example to .env and set TELEGRAM_BOT_TOKEN and ETHERSCAN_API_KEY."
  exit 1
fi

SSH_COMMON_OPTS=(
  -o StrictHostKeyChecking=no
  -o UserKnownHostsFile=/dev/null
  -o PreferredAuthentications=password
  -o PubkeyAuthentication=no
)

run_ssh() {
  # ssh uses lowercase -p for port
  sshpass -p "$PASSWORD" ssh "${SSH_COMMON_OPTS[@]}" -p "$SSH_PORT" "${REMOTE_USER}@${REMOTE_HOST}" "$@"
}

run_scp() {
  # scp uses uppercase -P for port (-p means preserve times)
  sshpass -p "$PASSWORD" scp "${SSH_COMMON_OPTS[@]}" -P "$SSH_PORT" "$@"
}

echo "==> Checking SSH connectivity to ${REMOTE_USER}@${REMOTE_HOST}:${SSH_PORT}"
run_ssh "echo connected && (command -v docker >/dev/null && docker --version || echo 'DOCKER_MISSING')"

# Resolve deploy dir on the remote host (default: $HOME/bsc-alert-bot).
if [[ -z "$REMOTE_DIR" ]]; then
  REMOTE_DIR="$(run_ssh 'printf %s "$HOME/bsc-alert-bot"')"
else
  # Expand ~ on the remote side.
  REMOTE_DIR="$(run_ssh "bash -lc 'echo $(printf '%q' "$REMOTE_DIR")'")"
fi
REMOTE_DIR="$(echo "$REMOTE_DIR" | tr -d '\r')"
if [[ -z "$REMOTE_DIR" ]]; then
  echo "Error: could not resolve remote deploy directory." >&2
  exit 1
fi
echo "==> Remote deploy directory: ${REMOTE_DIR}"

echo "==> Ensuring Docker is usable on remote host"
run_ssh 'bash -s' <<'REMOTE_DOCKER_CHECK'
set -euo pipefail
if ! command -v docker >/dev/null 2>&1; then
  echo "Docker not found. Installing Docker requires root; ask an admin or re-run as root." >&2
  exit 1
fi
if ! docker info >/dev/null 2>&1; then
  echo "Error: Docker is installed but not usable by this user." >&2
  echo "Add the user to the docker group, then re-login:" >&2
  echo "  sudo usermod -aG docker \$USER" >&2
  exit 1
fi
if ! docker compose version >/dev/null 2>&1; then
  if command -v docker-compose >/dev/null 2>&1; then
    echo "Using docker-compose (standalone)."
  else
    echo "Error: docker compose plugin not available." >&2
    exit 1
  fi
fi
REMOTE_DOCKER_CHECK

echo "==> Creating remote directory ${REMOTE_DIR}"
run_ssh "mkdir -p $(printf '%q' "$REMOTE_DIR")"

echo "==> Uploading project files"
TMP_ARCHIVE="$(mktemp -t bsc-alert-bot.XXXXXX.tar.gz)"
cleanup() { rm -f "$TMP_ARCHIVE"; }
trap cleanup EXIT

tar -czf "$TMP_ARCHIVE" \
  -C "$SCRIPT_DIR" \
  Dockerfile \
  docker-compose.yml \
  requirements.txt \
  main.py \
  .env

run_scp "$TMP_ARCHIVE" "${REMOTE_USER}@${REMOTE_HOST}:/tmp/bsc-alert-bot.tar.gz"
run_ssh "tar -xzf /tmp/bsc-alert-bot.tar.gz -C $(printf '%q' "$REMOTE_DIR") && rm -f /tmp/bsc-alert-bot.tar.gz"

echo "==> Building and starting container"
run_ssh "cd $(printf '%q' "$REMOTE_DIR") && (docker compose up -d --build || docker-compose up -d --build)"

echo "==> Container status"
run_ssh "cd $(printf '%q' "$REMOTE_DIR") && (docker compose ps || docker-compose ps) && docker logs --tail 40 bsc-alert-bot || true"

echo
echo "Deploy complete on ${REMOTE_HOST}."
echo "Useful commands:"
echo "  ssh ${REMOTE_USER}@${REMOTE_HOST} 'docker logs -f bsc-alert-bot'"
echo "  ssh ${REMOTE_USER}@${REMOTE_HOST} 'cd ${REMOTE_DIR} && docker compose restart'"
