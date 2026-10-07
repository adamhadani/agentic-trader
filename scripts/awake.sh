#!/usr/bin/env bash
# Keep the host awake during US market hours (Mon-Fri 09:15-16:30 America/New_York).
#
# Run hourly by the com.agentictrader.awake launch agent. The window is computed in
# America/New_York, never in local offsets, because US and local DST dates differ.
# Outside the window the script exits 0 silently. Market holidays are not detected:
# the machine simply stays awake on a holiday, which is harmless.
#
# caffeinate -i prevents idle sleep (also on battery); -s prevents system sleep but
# only holds while on AC power. Overlapping assertions from repeated runs are harmless.
#
# Test hooks: AWAKE_NOW_UTC (ISO 8601, e.g. 2026-11-03T15:00:00Z) overrides the clock;
# AWAKE_DRY_RUN=1 prints the seconds to hold instead of running caffeinate.
set -euo pipefail

WINDOW_START=$((9 * 3600 + 15 * 60))
WINDOW_END=$((16 * 3600 + 30 * 60))

if [ -n "${AWAKE_NOW_UTC:-}" ]; then
    if now_epoch="$(date -j -u -f "%Y-%m-%dT%H:%M:%SZ" "$AWAKE_NOW_UTC" +%s 2>/dev/null)"; then
        :
    else
        now_epoch="$(date -u -d "$AWAKE_NOW_UTC" +%s)"
    fi
else
    now_epoch="$(date +%s)"
fi

if et_fields="$(TZ=America/New_York date -r "$now_epoch" "+%u %H %M %S" 2>/dev/null)"; then
    :
else
    et_fields="$(TZ=America/New_York date -d "@$now_epoch" "+%u %H %M %S")"
fi
read -r weekday hour minute second <<<"$et_fields"

if [ "$weekday" -gt 5 ]; then
    exit 0
fi

seconds_of_day=$((10#$hour * 3600 + 10#$minute * 60 + 10#$second))
if [ "$seconds_of_day" -lt "$WINDOW_START" ] || [ "$seconds_of_day" -ge "$WINDOW_END" ]; then
    exit 0
fi

remaining=$((WINDOW_END - seconds_of_day))
if [ "${AWAKE_DRY_RUN:-}" = "1" ]; then
    echo "$remaining"
    exit 0
fi
exec /usr/bin/caffeinate -i -s -t "$remaining"
