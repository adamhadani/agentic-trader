#!/usr/bin/env bash
set -euo pipefail

PLIST_NAME="com.agentictrader.copilot"
LAUNCH_AGENTS_DIR="$HOME/Library/LaunchAgents"
TARGET_PLIST="$LAUNCH_AGENTS_DIR/$PLIST_NAME.plist"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
DATA_DIR="$REPO_DIR/data"
mkdir -p "$DATA_DIR"

UV_BIN="$(which uv || echo "$HOME/.local/bin/uv")"

generate_plist() {
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
    <true/>
    <key>StandardOutPath</key>
    <string>$DATA_DIR/copilot.log</string>
    <key>StandardErrorPath</key>
    <string>$DATA_DIR/copilot.err.log</string>
</dict>
</plist>
EOF
    echo "Generated $TARGET_PLIST"
}

case "${1:-status}" in
    install)
        generate_plist
        launchctl unload "$TARGET_PLIST" 2>/dev/null || true
        launchctl load "$TARGET_PLIST"
        echo "Service $PLIST_NAME installed and loaded."
        ;;
    uninstall)
        launchctl unload "$TARGET_PLIST" 2>/dev/null || true
        rm -f "$TARGET_PLIST"
        echo "Service $PLIST_NAME unloaded and removed."
        ;;
    start)
        launchctl start "$PLIST_NAME"
        echo "Sent start signal to $PLIST_NAME"
        ;;
    stop)
        launchctl stop "$PLIST_NAME"
        echo "Sent stop signal to $PLIST_NAME"
        ;;
    status)
        echo "Checking launchctl status for $PLIST_NAME..."
        launchctl list | grep "$PLIST_NAME" || echo "Service not currently registered or running in launchd."
        ;;
    logs)
        tail -n 50 -f "$DATA_DIR/copilot.log"
        ;;
    *)
        echo "Usage: $0 {install|uninstall|start|stop|status|logs}"
        exit 1
        ;;
esac
