#!/bin/bash
# agent-team-os-collect.sh — one bus collection round with the other machines (v2.5).
# Pushes what is queued in outbox/<host>/ and pulls what <host> left for us.
# Run by launchd every 60 s on the laptop (install.sh --host-sync), so sessions
# that never touch the bus still get their mail. Safe to run by hand any time.
#   agent-team-os-collect.sh [host]
LIB="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/agent-team-os-lib.sh"
# shellcheck source=/dev/null
source "$LIB"
# Log only what happened: an idle round every minute would fill the log with noise.
ab_collect_outbox --force "${1:-}" \
  | grep -v ' ok pushed=0 pulled=0 queued=0$' \
  | sed "s/^/$(ab_iso_now) /"
exit 0
