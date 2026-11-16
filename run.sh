#!/usr/bin/env bash
# Restart the desk and the plan B publisher as session-leader daemons.
# nohup alone is not enough on this box: the processes stay in the launching
# shell's session and in the /agent cgroup. setsid gives them their own
# session; moving them into /sys/fs/cgroup/background keeps them scheduled
# after the *agent process* ends, as long as the box itself is still running.
#
# LIMITATION: this is an anyrun pod. When no agent or routine session is
# active, the platform hibernates the whole VM (see /var/run/anyrun-hibernate-hook
# and sand-host "box was paused or frozen"). Guest processes, including those
# in /background, do not run during hibernation. There is no in-box way to
# stay awake. A more frequent desk-health routine can only wake the box for
# the length of that run.
cd "$(dirname "$0")"
pkill -f "\.venv/bin/python main.py" || true
pkill -f "\.venv/bin/python publisher.py" || true
sleep 1
mkdir -p logs

# $1 = script name (main.py / publisher.py). Starts a new session, ignores
# SIGHUP, joins the background cgroup when possible, then execs python.
start_daemon() {
  local script="$1"
  local base="${script%.py}"
  local pidfile="logs/${base}.pid"
  rm -f "$pidfile"
  setsid -f /bin/bash -c "
    trap '' HUP
    exec >>'logs/${base}.out' 2>&1
    echo \$\$ > 'logs/${base}.pid'
    if [ -e /sys/fs/cgroup/background/cgroup.procs ]; then
      printf '%s\n' \$\$ | sudo -n tee /sys/fs/cgroup/background/cgroup.procs >/dev/null \
        || printf '%s\n' \$\$ > /sys/fs/cgroup/background/cgroup.procs \
        || echo 'warn: could not join /sys/fs/cgroup/background; desk may freeze when the agent session ends'
    fi
    exec '.venv/bin/python' '$script'
  "
  local i=0
  while [ ! -s "$pidfile" ] && [ "$i" -lt 50 ]; do
    sleep 0.1
    i=$((i + 1))
  done
  if [ -s "$pidfile" ]; then
    echo "$script pid $(tr -d ' \n' < "$pidfile")"
  else
    echo "$script failed to start" >&2
    return 1
  fi
}

start_daemon main.py
start_daemon publisher.py
