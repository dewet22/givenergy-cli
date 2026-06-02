#!/usr/bin/env bash
# Scan /tmp/givenergy-coordination for new messages addressed to the cli agent.
# Outputs a summary of any new messages; always exits 0 (non-blocking).

INBOX="/tmp/givenergy-coordination"
SEEN_FILE="$(git rev-parse --show-toplevel 2>/dev/null)/.claude/.inbox-seen"

[ -d "$INBOX" ] || exit 0

# Find cli-addressed messages newer than the seen marker (all, if no marker yet)
if [ -f "$SEEN_FILE" ]; then
    NEW=$(find "$INBOX" -name "*-cli-*.md" -newer "$SEEN_FILE" 2>/dev/null | sort)
else
    NEW=$(find "$INBOX" -name "*-cli-*.md" 2>/dev/null | sort)
fi

if [ -n "$NEW" ]; then
    echo "📬 New coordination inbox message(s) for cli agent:"
    while IFS= read -r f; do
        echo "  • $(basename "$f")"
        # Show the first heading as a preview
        grep -m1 "^#" "$f" 2>/dev/null | sed 's/^/    /' || true
    done <<< "$NEW"
fi

# Advance the seen marker regardless of whether there were new messages
touch "$SEEN_FILE"
exit 0
