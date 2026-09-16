#!/usr/bin/env bash
set -euo pipefail

PLIST_NAME="com.agentictrader.copilot"
WATCHDOG_PLIST_NAME="com.agentictrader.watchdog"
MINER_PLIST_NAME="com.agentictrader.alphaminer"
LAUNCH_AGENTS_DIR="$HOME/Library/LaunchAgents"
TARGET_PLIST="$LAUNCH_AGENTS_DIR/$PLIST_NAME.plist"
TARGET_WATCHDOG_PLIST="$LAUNCH_AGENTS_DIR/$WATCHDOG_PLIST_NAME.plist"
TARGET_MINER_PLIST="$LAUNCH_AGENTS_DIR/$MINER_PLIST_NAME.plist"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
DATA_DIR="$REPO_DIR/data"
mkdir -p "$DATA_DIR"

UV_BIN="$(which uv || echo "$HOME/.local/bin/uv")"
USER_ID="$(id -u)"

generate_copilot_plist() {
    mkdir -p "$LAUNCH_AGENTS_DIR"
    cat <<EOF > "$TARGET_PLIST"
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>$PLIST_NAME</string>
    <key>WorkingDirectory</key>
    <string>$REPO_DIR</string>
    <key>ProgramArguments</key>
    <array>
        <string>/bin/zsh</string>
        <string>-l</string>
        <string>-c</string>
        <string>cd "$REPO_DIR" &amp;&amp; if [ -f .envrc ]; then set -a; source .envrc; set +a; fi &amp;&amp; exec "$UV_BIN" run copilot daemon</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <dict>
        <key>SuccessfulExit</key>
        <false/>
        <key>Crashed</key>
        <true/>
    </dict>
    <key>StandardOutPath</key>
    <string>$DATA_DIR/copilot.log</string>
    <key>StandardErrorPath</key>
    <string>$DATA_DIR/copilot.err.log</string>
</dict>
</plist>
EOF
    echo "Generated $TARGET_PLIST"
}

generate_watchdog_plist() {
    mkdir -p "$LAUNCH_AGENTS_DIR"
    cat <<EOF > "$TARGET_WATCHDOG_PLIST"
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>$WATCHDOG_PLIST_NAME</string>
    <key>WorkingDirectory</key>
    <string>$REPO_DIR</string>
    <key>ProgramArguments</key>
    <array>
        <string>/bin/zsh</string>
        <string>-l</string>
        <string>-c</string>
        <string>"$SCRIPT_DIR/launchd.sh" watchdog</string>
    </array>
    <key>StartInterval</key>
    <integer>60</integer>
    <key>StandardOutPath</key>
    <string>$DATA_DIR/watchdog.log</string>
    <key>StandardErrorPath</key>
    <string>$DATA_DIR/watchdog.err.log</string>
</dict>
</plist>
EOF
    echo "Generated $TARGET_WATCHDOG_PLIST"
}

generate_alphaminer_plist() {
    mkdir -p "$LAUNCH_AGENTS_DIR"
    cat <<EOF > "$TARGET_MINER_PLIST"
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>$MINER_PLIST_NAME</string>
    <key>WorkingDirectory</key>
    <string>$REPO_DIR</string>
    <key>ProgramArguments</key>
    <array>
        <string>/bin/zsh</string>
        <string>-l</string>
        <string>-c</string>
        <string>cd "$REPO_DIR" &amp;&amp; if [ -f .envrc ]; then set -a; source .envrc; set +a; fi &amp;&amp; exec "$UV_BIN" run copilot alpha mine --symbols NVDA,AMD,AAPL,MSFT,QQQ,SPY,GOOGL,AMZN,META,TSLA --iterations 25</string>
    </array>
    <key>StartCalendarInterval</key>
    <dict>
        <key>Weekday</key>
        <integer>6</integer>
        <key>Hour</key>
        <integer>2</integer>
        <key>Minute</key>
        <integer>0</integer>
    </dict>
    <key>StandardOutPath</key>
    <string>$DATA_DIR/alphaminer.log</string>
    <key>StandardErrorPath</key>
    <string>$DATA_DIR/alphaminer.err.log</string>
</dict>
</plist>
EOF
    echo "Generated $TARGET_MINER_PLIST"
}

run_watchdog_probe() {
    local now
    now="$(date -u +"%Y-%m-%dT%H:%M:%SZ")"

    # Check if copilot process is registered and active
    local status_line
    status_line="$(launchctl list | grep "$PLIST_NAME" || true)"

    if [ -z "$status_line" ]; then
        echo "[$now] [WATCHDOG WARNING] $PLIST_NAME not found in launchd list. Reloading service..." >> "$DATA_DIR/watchdog.log"
        launchctl load "$TARGET_PLIST" >> "$DATA_DIR/watchdog.log" 2>&1 || true
        status_line="$(launchctl list | grep "$PLIST_NAME" || true)"
    fi

    local pid
    pid="$(echo "$status_line" | awk '{print $1}')"
    local last_exit
    last_exit="$(echo "$status_line" | awk '{print $2}')"

    if [ -z "$status_line" ]; then
        echo "[$now] [WATCHDOG ALERT] Service registration is still missing." >&2
    elif [ "$pid" = "-" ]; then
        echo "[$now] [WATCHDOG ALERT] $PLIST_NAME is registered but not running (last exit code: $last_exit). Restarting..." >> "$DATA_DIR/watchdog.log"
        launchctl kickstart -k "gui/$USER_ID/$PLIST_NAME" >> "$DATA_DIR/watchdog.log" 2>&1 || launchctl start "$PLIST_NAME"
    else
        echo "[$now] [WATCHDOG OK] $PLIST_NAME is alive (PID: $pid)." >> "$DATA_DIR/watchdog.log"
    fi
    # Read the same passive contract as doctor/readiness. This process can record
    # an incident even when the daemon's event loop is stalled; it never polls
    # Telegram or executes trades. Unready means alert, not a blind restart.
    (cd "$REPO_DIR" && "$UV_BIN" run copilot doctor --monitor) || {
        echo "[$now] [WATCHDOG] Readiness monitoring reported a failure; inspect diagnostics above." >&2
    }

}

case "${1:-status}" in
    install)
        generate_copilot_plist
        generate_watchdog_plist
        generate_alphaminer_plist
        launchctl unload "$TARGET_PLIST" 2>/dev/null || true
        launchctl load "$TARGET_PLIST"
        launchctl unload "$TARGET_WATCHDOG_PLIST" 2>/dev/null || true
        launchctl load "$TARGET_WATCHDOG_PLIST"
        launchctl unload "$TARGET_MINER_PLIST" 2>/dev/null || true
        launchctl load "$TARGET_MINER_PLIST"
        echo "Services $PLIST_NAME, $WATCHDOG_PLIST_NAME, and $MINER_PLIST_NAME installed and loaded."
        ;;
    uninstall)
        launchctl unload "$TARGET_MINER_PLIST" 2>/dev/null || true
        rm -f "$TARGET_MINER_PLIST"
        launchctl unload "$TARGET_WATCHDOG_PLIST" 2>/dev/null || true
        rm -f "$TARGET_WATCHDOG_PLIST"
        launchctl unload "$TARGET_PLIST" 2>/dev/null || true
        rm -f "$TARGET_PLIST"
        echo "Services $PLIST_NAME, $WATCHDOG_PLIST_NAME, and $MINER_PLIST_NAME unloaded and removed."
        ;;
    install-miner)
        generate_alphaminer_plist
        launchctl unload "$TARGET_MINER_PLIST" 2>/dev/null || true
        launchctl load "$TARGET_MINER_PLIST"
        echo "Scheduled alpha miner $MINER_PLIST_NAME installed and loaded."
        ;;
    uninstall-miner)
        launchctl unload "$TARGET_MINER_PLIST" 2>/dev/null || true
        rm -f "$TARGET_MINER_PLIST"
        echo "Scheduled alpha miner $MINER_PLIST_NAME unloaded and removed."
        ;;
    run-miner)
        echo "Triggering offline alpha mining run..."
        cd "$REPO_DIR" && if [ -f .envrc ]; then set -a; source .envrc; set +a; fi && "$UV_BIN" run copilot alpha mine --symbols NVDA,AMD,AAPL,MSFT,QQQ,SPY,GOOGL,AMZN,META,TSLA --iterations 25
        ;;
    start)
        launchctl start "$PLIST_NAME"
        echo "Sent start signal to $PLIST_NAME"
        ;;
    stop)
        launchctl stop "$PLIST_NAME"
        echo "Sent stop signal to $PLIST_NAME"
        ;;
    restart)
        echo "Restarting $PLIST_NAME..."
        launchctl kickstart -k "gui/$USER_ID/$PLIST_NAME" 2>/dev/null || (launchctl stop "$PLIST_NAME" && sleep 1 && launchctl start "$PLIST_NAME")
        echo "Restart signal sent to $PLIST_NAME"
        ;;
    status)
        echo "=== launchd Service Status ==="
        echo "Daemon ($PLIST_NAME):"
        launchctl list | grep "$PLIST_NAME" || echo "  Not currently registered in launchd."
        echo "Watchdog ($WATCHDOG_PLIST_NAME):"
        launchctl list | grep "$WATCHDOG_PLIST_NAME" || echo "  Not currently registered in launchd."
        echo "Alpha Miner ($MINER_PLIST_NAME):"
        launchctl list | grep "$MINER_PLIST_NAME" || echo "  Not currently registered in launchd."
        ;;
    health)
        echo "Running Copilot Healthcheck..."
        cd "$REPO_DIR" && "$UV_BIN" run copilot doctor
        ;;
    watchdog)
        run_watchdog_probe
        ;;
    logs)
        tail -n 50 -f "$DATA_DIR/copilot.log"
        ;;
    watchdog-logs)
        tail -n 50 -f "$DATA_DIR/watchdog.log"
        ;;
    miner-logs)
        tail -n 50 -f "$DATA_DIR/alphaminer.log"
        ;;
    *)
        echo "Usage: $0 {install|uninstall|install-miner|uninstall-miner|run-miner|start|stop|restart|status|health|watchdog|logs|watchdog-logs|miner-logs}"
        exit 1
        ;;
esac
