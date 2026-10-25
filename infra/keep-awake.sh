#!/bin/sh
# Prevent idle system sleep on macOS while Market Truffler is collecting.
#
#   infra/keep-awake.sh start             start protection (no-op if already active)
#   infra/keep-awake.sh stop              stop it (no-op if not running)
#   infra/keep-awake.sh status            real state; exit 0 active, 1 inactive, 2 unknown
#   infra/keep-awake.sh enable-at-login   opt in: also start it at every login
#   infra/keep-awake.sh disable-at-login  undo that
#
# Protection is a per-user launchd agent (label below), never a child of
# the terminal: closing the terminal, or recreating any container, does not
# end it, and launchd restarts it if it dies. The agent runs this script's
# `run` mode, which holds macOS's own `caffeinate -i -s` (-i prevents idle
# sleep, -s system sleep while on AC power; tied to the agent with -w, so it
# can never outlive it) and, every minute, checks that macOS has actually
# registered caffeinate's assertions and writes the result to
# var/host-status/keep-awake.json — the only host state the api container
# sees (read-only mount), shown in Vitals.
#
# Ends at logout and does not come back after a reboot (a macOS update
# reboots too) unless enable-at-login was run. No root, no system daemon,
# no `pmset` setting is ever changed. Only this agent's own caffeinate is
# ever stopped — other caffeinate processes are never touched.
#
# What it does NOT prevent: closing a MacBook's lid (sleeps anyway unless
# in clamshell mode with external display + power), shutdown, restart,
# logout, power loss, or Docker itself stopping.
set -eu

LABEL=com.market-truffler.keep-awake
REPO=$(cd "$(dirname "$0")/.." && pwd)
SCRIPT="$REPO/infra/keep-awake.sh"
STATUS_DIR="$REPO/var/host-status"
STATUS_FILE="$STATUS_DIR/keep-awake.json"
DOMAIN="gui/$(id -u)"
AGENT_DIR="$HOME/Library/Application Support/market-truffler"
SESSION_PLIST="$AGENT_DIR/$LABEL.plist"
LOGIN_PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
LOG="$AGENT_DIR/keep-awake.log"
INTERVAL=60
# Before launchd: a detached `nohup caffeinate -i -s` recorded here.
LEGACY_PIDFILE="${TMPDIR:-/tmp}/market-truffler-keep-awake.pid"

# -- helpers -----------------------------------------------------------------

agent_pid() {
    launchctl print "$DOMAIN/$LABEL" 2>/dev/null | awk '$1 == "pid" && $2 == "=" {print $3; exit}'
}

agent_loaded() {
    launchctl print "$DOMAIN/$LABEL" >/dev/null 2>&1
}

# The agent's own caffeinate: a child of the agent process, never matched by name.
caffeinate_pid() {
    pid=$(agent_pid)
    [ -n "$pid" ] || return 1
    pgrep -P "$pid" -x caffeinate | head -n 1
}

assertions_held() {
    out=$(pmset -g assertions)
    printf '%s\n' "$out" | grep -q "pid $1(caffeinate): .* PreventUserIdleSystemSleep " &&
        printf '%s\n' "$out" | grep -q "pid $1(caffeinate): .* PreventSystemSleep "
}

power_source() {
    pmset -g batt | head -n 1 | sed "s/.*'\(.*\)'.*/\1/"
}

write_status() {
    mkdir -p "$STATUS_DIR"
    tmp="$STATUS_DIR/.keep-awake.json.tmp-$$"
    printf '{"schema": 1, "observed_at": "%s", "state": "%s", "detail": "%s", "power_source": "%s", "interval_seconds": %s}\n' \
        "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$1" "$2" "$(power_source)" "$INTERVAL" >"$tmp"
    mv -f "$tmp" "$STATUS_FILE"
}

write_plist() {
    mkdir -p "$(dirname "$1")"
    cat >"$1" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key>
  <array><string>/bin/sh</string><string>$SCRIPT</string><string>run</string></array>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>ProcessType</key><string>Interactive</string>
  <key>StandardOutPath</key><string>$LOG</string>
  <key>StandardErrorPath</key><string>$LOG</string>
</dict>
</plist>
EOF
}

# Wait up to $1 seconds for the agent's caffeinate to hold both assertions.
wait_active() {
    i=0
    while [ "$i" -lt "$1" ]; do
        if cpid=$(caffeinate_pid) && [ -n "$cpid" ] && assertions_held "$cpid"; then
            echo "$cpid"
            return 0
        fi
        sleep 1
        i=$((i + 1))
    done
    return 1
}

# The pre-launchd detached process, identified by its pidfile and exact command only.
stop_legacy() {
    [ -f "$LEGACY_PIDFILE" ] || return 0
    pid=$(cat "$LEGACY_PIDFILE")
    if [ -n "$pid" ] && [ "$(ps -o command= -p "$pid" 2>/dev/null)" = "caffeinate -i -s" ]; then
        kill "$pid"
        echo "stopped the previous detached keep-awake process (pid $pid)"
    fi
    rm -f "$LEGACY_PIDFILE"
}

# -- commands ----------------------------------------------------------------

case "${1:-status}" in
run)
    # launchd's entry point; not for interactive use.
    /usr/bin/caffeinate -i -s -w $$ &
    cpid=$!
    spid=
    trap 'write_status stopped "stopped by the operator"; kill "$cpid" $spid 2>/dev/null; exit 0' TERM INT HUP
    sleep 1
    while :; do
        if ! kill -0 "$cpid" 2>/dev/null; then
            write_status inactive "caffeinate exited; launchd restarts the agent"
            exit 1
        fi
        if assertions_held "$cpid"; then
            write_status active "sleep-prevention assertions registered"
        else
            write_status inactive "caffeinate running but macOS shows no assertion"
        fi
        sleep "$INTERVAL" &
        spid=$!
        wait "$spid" || true
        spid=
    done
    ;;
start)
    if cpid=$(wait_active 1); then
        echo "keep-awake already active (caffeinate pid $cpid)"
        stop_legacy
        exit 0
    fi
    if agent_loaded; then
        # Loaded but not holding the assertions: start it over.
        launchctl bootout "$DOMAIN/$LABEL" 2>/dev/null || true
        sleep 1
    fi
    if [ -f "$LOGIN_PLIST" ]; then plist=$LOGIN_PLIST; else
        plist=$SESSION_PLIST
        write_plist "$plist"
    fi
    launchctl bootstrap "$DOMAIN" "$plist"
    if ! cpid=$(wait_active 10); then
        echo "keep-awake FAILED to start: no sleep-prevention assertion registered" >&2
        echo "see: launchctl print $DOMAIN/$LABEL; $LOG" >&2
        exit 1
    fi
    echo "keep-awake active (launchd agent $LABEL, caffeinate pid $cpid; assertions verified)"
    stop_legacy
    ;;
stop)
    if agent_loaded; then
        launchctl bootout "$DOMAIN/$LABEL"
        i=0
        while agent_loaded && [ "$i" -lt 10 ]; do
            sleep 1
            i=$((i + 1))
        done
        if agent_loaded; then
            echo "keep-awake did not stop: $LABEL is still loaded" >&2
            exit 1
        fi
        echo "keep-awake stopped; normal sleep settings apply again"
    else
        echo "keep-awake was not active"
    fi
    stop_legacy
    if [ -f "$LOGIN_PLIST" ]; then
        echo "note: enabled at login — it starts again at the next login (disable-at-login)"
    fi
    ;;
status)
    if cpid=$(wait_active 1); then
        echo "keep-awake: ACTIVE (launchd agent $LABEL, caffeinate pid $cpid, agent pid $(agent_pid))"
        pmset -g assertions | grep "pid $cpid(caffeinate)" || true
        code=0
    elif agent_loaded; then
        echo "keep-awake: UNKNOWN — agent loaded but no verified assertion (see $LOG)"
        code=2
    else
        echo "keep-awake: INACTIVE — idle sleep will interrupt collection"
        code=1
    fi
    if [ -f "$LEGACY_PIDFILE" ]; then
        echo "previous detached process still recorded (pid $(cat "$LEGACY_PIDFILE")); 'start' replaces it"
    fi
    if [ -f "$STATUS_FILE" ]; then
        echo "last report for Vitals: $(cat "$STATUS_FILE")"
    fi
    if [ -f "$LOGIN_PLIST" ]; then
        echo "at login: enabled"
    else
        echo "at login: not enabled (start again after every login/reboot)"
    fi
    echo "power source: $(power_source)" \
        "(-s only holds on AC power; on battery only idle sleep is prevented)"
    echo "system sleep setting: $(pmset -g | awk '$1 == "sleep" {print $2}') min idle"
    exit "$code"
    ;;
enable-at-login)
    write_plist "$LOGIN_PLIST"
    echo "enabled at login: $LOGIN_PLIST (launchd starts it at every login)"
    echo "this session: run '$0 start' if it is not active yet"
    ;;
disable-at-login)
    rm -f "$LOGIN_PLIST"
    echo "not enabled at login any more; a running agent keeps running until 'stop' or logout"
    ;;
*)
    echo "usage: $0 start|stop|status|enable-at-login|disable-at-login" >&2
    exit 2
    ;;
esac
