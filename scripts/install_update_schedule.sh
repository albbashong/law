#!/usr/bin/env bash
# 법령 DB 갱신(scheduled_update.sh)을 예약에 건다 — cron 이 있으면 cron, 없으면 systemd 타이머.
#   scripts/install_update_schedule.sh            # 기본: 매주 일요일 03:00
#   scripts/install_update_schedule.sh --remove   # 예약 지우기
#   CRON="0 3 * * 0" ON_CALENDAR="Sun *-*-* 03:00:00" scripts/install_update_schedule.sh   # 시각 바꾸기
#   COMPOSE="podman-compose" scripts/install_update_schedule.sh   # 재시작에 쓸 compose 도구를 고정
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
CRON="${CRON:-0 3 * * 0}"
ON_CALENDAR="${ON_CALENDAR:-Sun *-*-* 03:00:00}"
NAME="permit-law-update"
MARK="# $NAME"
JOB="$ROOT/scripts/scheduled_update.sh"
ENVS="${COMPOSE:+COMPOSE='$COMPOSE' }${LAW_MCP_PORT:+LAW_MCP_PORT=$LAW_MCP_PORT }"
chmod +x "$JOB" "$ROOT/scripts/update.sh"

if [ "$(id -u)" = 0 ]; then
  UNIT_DIR=/etc/systemd/system; SCTL=(systemctl)
else
  UNIT_DIR="$HOME/.config/systemd/user"; SCTL=(systemctl --user)
fi

if [ "${1:-}" = "--remove" ]; then
  if command -v crontab >/dev/null; then
    (crontab -l 2>/dev/null | grep -vF "$MARK") | crontab - || true
  fi
  if [ -f "$UNIT_DIR/$NAME.timer" ]; then
    "${SCTL[@]}" disable --now "$NAME.timer" || true
    rm -f "$UNIT_DIR/$NAME.timer" "$UNIT_DIR/$NAME.service"
    "${SCTL[@]}" daemon-reload || true
  fi
  echo "예약을 지웠습니다."
  exit 0
fi

if command -v crontab >/dev/null; then
  line="$CRON cd '$ROOT' && ${ENVS}'$JOB' >> '$ROOT/logs/scheduled_update.log' 2>&1 $MARK"
  (crontab -l 2>/dev/null | grep -vF "$MARK"; echo "$line") | crontab -
  echo "cron 에 걸었습니다: $CRON"
  crontab -l | grep -F "$MARK"
elif "${SCTL[@]}" list-timers >/dev/null 2>&1; then
  mkdir -p "$UNIT_DIR" "$ROOT/logs"
  cat > "$UNIT_DIR/$NAME.service" <<EOF
[Unit]
Description=permit-law 법령 DB 갱신

[Service]
Type=oneshot
WorkingDirectory=$ROOT
Environment=${COMPOSE:+COMPOSE=$COMPOSE}
Environment=${LAW_MCP_PORT:+LAW_MCP_PORT=$LAW_MCP_PORT}
ExecStart=$JOB
StandardOutput=append:$ROOT/logs/scheduled_update.log
StandardError=append:$ROOT/logs/scheduled_update.log
EOF
  cat > "$UNIT_DIR/$NAME.timer" <<EOF
[Unit]
Description=permit-law 법령 DB 갱신 예약

[Timer]
OnCalendar=$ON_CALENDAR
Persistent=true

[Install]
WantedBy=timers.target
EOF
  "${SCTL[@]}" daemon-reload
  "${SCTL[@]}" enable --now "$NAME.timer"
  echo "systemd 타이머를 걸었습니다: $ON_CALENDAR"
  "${SCTL[@]}" list-timers "$NAME.timer" --no-pager || true
  if [ "$(id -u)" != 0 ]; then
    echo "로그아웃 뒤에도 돌게 하려면 한 번: sudo loginctl enable-linger $USER"
  fi
else
  echo "cron 도 systemd 타이머도 쓸 수 없습니다. cron(cronie) 을 설치하거나 root 로 다시 실행하세요." >&2
  exit 1
fi
echo "바로 한 번 돌려 보려면: $JOB"
