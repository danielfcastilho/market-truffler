#!/bin/sh
# Prevent idle system sleep on macOS while Market Truffler is collecting.
#
#   infra/keep-awake.sh start    hold the assertions (no-op if already held)
#   infra/keep-awake.sh stop     release them
#   infra/keep-awake.sh status   show the real state; exit 0 only if active
#
# Uses only macOS's own `caffeinate -i -s`: -i prevents idle sleep, -s
# prevents system sleep while on AC power. The process is detached, so
# closing the terminal does not end it; it does not survive a reboot or
# logout (nothing is installed, no system setting is changed — `pmset`
# values stay exactly as they are). One process at most: a pidfile in
# $TMPDIR plus a check of the actual process command line, so a stale
# pidfile never makes it kill an unrelated process.
#
# What it does NOT prevent: closing a MacBook's lid (sleeps anyway unless
# in clamshell mode with external display + power), shutdown, restart,
# logout, power loss, or Docker itself stopping.
set -eu

PIDFILE="${TMPDIR:-/tmp}/market-truffler-keep-awake.pid"
COMMAND="caffeinate -i -s"

running_pid() {
    if [ -f "$PIDFILE" ]; then
        pid=$(cat "$PIDFILE")
        if [ -n "$pid" ] && [ "$(ps -o command= -p "$pid" 2>/dev/null)" = "$COMMAND" ]; then
            echo "$pid"
            return 0
        fi
        rm -f "$PIDFILE" # stale: the process is gone or the pid was reused
    fi
    # A previous run whose pidfile was lost ($TMPDIR cleaned): adopt it.
    pid=$(pgrep -x -f "$COMMAND" | head -n 1 || true)
    if [ -n "$pid" ]; then
        echo "$pid" >"$PIDFILE"
        echo "$pid"
        return 0
    fi
    return 1
}

case "${1:-status}" in
start)
    if pid=$(running_pid); then
        echo "keep-awake already active (pid $pid)"
        exit 0
    fi
    nohup $COMMAND >/dev/null 2>&1 &
    pid=$!
    echo "$pid" >"$PIDFILE"
    sleep 1
    if [ "$(ps -o command= -p "$pid" 2>/dev/null)" != "$COMMAND" ]; then
        rm -f "$PIDFILE"
        echo "keep-awake failed to start" >&2
        exit 1
    fi
    echo "keep-awake active (pid $pid)"
    ;;
stop)
    if pid=$(running_pid); then
        kill "$pid"
        rm -f "$PIDFILE"
        echo "keep-awake stopped (pid $pid); normal sleep settings apply again"
    else
        echo "keep-awake was not active"
    fi
    ;;
status)
    if pid=$(running_pid); then
        echo "keep-awake: ACTIVE (pid $pid, running $(ps -o etime= -p "$pid" | tr -d ' '))"
        pmset -g assertions | grep "pid $pid(caffeinate)" || true
        active=0
    else
        echo "keep-awake: INACTIVE — idle sleep will interrupt collection"
        active=1
    fi
    echo "power source: $(pmset -g batt | head -n 1 | sed "s/.*'\(.*\)'.*/\1/")" \
        "(-s only holds on AC power; on battery only idle sleep is prevented)"
    echo "system sleep setting: $(pmset -g | awk '$1 == "sleep" {print $2}') min idle"
    exit "$active"
    ;;
*)
    echo "usage: $0 start|stop|status" >&2
    exit 2
    ;;
esac
