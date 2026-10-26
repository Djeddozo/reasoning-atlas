# Shared by restart.sh and schedules.sh: is Atlas run by its systemd user unit (hq.service)?
# Cron jobs have no user session, so `systemctl --user` could not reach the user bus there and every
# check failed: restart.sh then started its own `hq serve`/`hq bot` outside systemd (stray copies held
# port 8770 and crash-looped hq.service), and schedules.sh put the @reboot serve and bot watchdog lines
# back into cron. Point systemctl at the user bus explicitly, and treat an installed unit file as
# "managed" even when systemctl still cannot answer.
export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"
HQ_UNIT_FILE="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user/hq.service"

hq_managed() {
  systemctl --user is-enabled --quiet hq.service 2>/dev/null && return 0
  [ -f "$HQ_UNIT_FILE" ]
}

# Stop `hq serve` / `hq bot` processes that run outside hq.service (left by cron or an old restart).
hq_kill_strays() {
  local pid
  for pid in $(pgrep -f 'python3 -m (hq|engine) (serve|bot)$' 2>/dev/null); do
    grep -q '/hq.service' "/proc/$pid/cgroup" 2>/dev/null && continue
    kill "$pid" 2>/dev/null && echo "$(date '+%F %T') stopped stray Atlas process $pid (outside hq.service)"
  done
}
