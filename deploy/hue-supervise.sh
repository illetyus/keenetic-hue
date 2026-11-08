#!/bin/sh
component=$1
ROOT=$2
release=$3
ownerfd=$4
case "$component" in agent|tunnel) ;; *) exit 1 ;; esac
case "$release" in ''|*[!a-z0-9.-]*) exit 1 ;; esac
case "$ownerfd" in ''|*[!0-9]*) exit 1 ;; esac
[ -e "/proc/$$/fd/$ownerfd" ] || exit 1
umask 077
PIDFILE="$ROOT/run/$component-supervisor.json"
CHILDPID="$ROOT/run/$component-child.pid"
CHILDIDENTITY="$ROOT/run/$component-child.json"
child=''; sleeper=''; exit_reason='OBSERVED_EXIT'; exit_signal=''
boot=$(cat /proc/sys/kernel/random/boot_id) || exit 1
ticks=$(awk '{print $22}' "/proc/$$/stat") || exit 1
case "$ticks" in ''|*[!0-9]*) exit 1 ;; esac
printf '{"schema":1,"pid":%s,"releaseId":"%s","bootId":"%s","startTicks":"%s","ownerFd":%s}\n' "$$" "$release" "$boot" "$ticks" "$ownerfd" > "$PIDFILE.new-$$"
mv "$PIDFILE.new-$$" "$PIDFILE" || exit 1
log_event() {
    event=$1; reason=$2; code=${3:-0}; signame=${4:-}
    read mono rest < /proc/uptime || mono=0
    row=$(printf '{"schema":1,"component":"%s","event":"%s","stage":"supervisor","reason":"%s","bootId":"%s","pid":%s,"startTicks":"%s","releaseId":"%s","utc":"%s","monotonic":%s,"exitCode":%s,"signal":"%s","retrySeconds":%s}' "$component" "$event" "$reason" "$boot" "$$" "$ticks" "$release" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$mono" "$code" "$signame" "${delay:-0}")
    log="$ROOT/logs/$component-supervisor.log"
    if [ ! -L "$log" ] && [ ! -L "$log.previous" ] && { [ ! -e "$log" ] || [ -f "$log" ]; } && { [ ! -e "$log.previous" ] || [ -f "$log.previous" ]; }; then
        append_log=1
        if [ -f "$log" ] && [ "$(wc -c < "$log")" -ge 64000 ]; then
            mv "$log" "$log.previous" 2>/dev/null || append_log=0
        fi
        if [ "$append_log" = 1 ]; then printf '%s\n' "$row" >> "$log" 2>/dev/null || :; fi
    fi
    if [ "$event" = supervisor_exit ]; then
        terminal="$ROOT/run/$component-terminal.json"
        if [ ! -e "$terminal.new-$$" ] && [ ! -L "$terminal.new-$$" ]; then
            printf '%s\n' "$row" > "$terminal.new-$$" 2>/dev/null && mv "$terminal.new-$$" "$terminal" 2>/dev/null
        fi
    fi
    return 0
}
finish() {
    result=$1
    trap - 0
    log_event supervisor_exit "$exit_reason" "$result" "$exit_signal"
}
cleanup() {
    trap '' TERM INT
    exit_signal=$1; exit_reason="OBSERVED_$1"
    log_event supervisor_signal "$exit_reason" 0 "$exit_signal"
    if [ -n "$sleeper" ]; then kill -TERM "$sleeper" 2>/dev/null; wait "$sleeper" 2>/dev/null; fi
    if [ -n "$child" ]; then kill -TERM "$child" 2>/dev/null; wait "$child" 2>/dev/null; fi
    rm -f "$CHILDPID" "$CHILDIDENTITY"
    exit 0
}
trap 'finish $?' 0
trap 'cleanup TERM' TERM
trap 'cleanup INT' INT
log_event supervisor_start OK 0 ''
delay=2
while :; do
    started=$(date +%s)
    bootstrap="$ROOT/logs/$component-bootstrap.log"
    [ ! -f "$bootstrap" ] || mv "$bootstrap" "$bootstrap.previous"
    "$HUE_PYTHON" -I "$ROOT/releases/$release/service/component-child.py" "$component" "$ROOT" "$release" "$ownerfd" </dev/null >>"$bootstrap" 2>&1 &
    child=$!; echo "$child" > "$CHILDPID.new-$$"; mv "$CHILDPID.new-$$" "$CHILDPID"
    wait "$child"; result=$?; child=''; rm -f "$CHILDPID" "$CHILDIDENTITY"
    if [ $(($(date +%s)-started)) -ge 60 ]; then delay=2; fi
    log_event child_exit CHILD_EXIT "$result"
    sleep "$delay" & sleeper=$!; wait "$sleeper"; sleeper=''
    delay=$((delay*2)); [ "$delay" -le 30 ] || delay=30
done
