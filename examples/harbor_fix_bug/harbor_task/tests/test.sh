#!/usr/bin/env bash
set -euo pipefail

RESULT=$(python /app/sum_evens.py)

# Harbor expects reward here
REWARD_FILE="/logs/verifier/reward.json"

if [ "$RESULT" = "12" ]; then
    echo "PASS: sum_evens([1,2,3,4,5,6]) = 12"
    echo '{"score": 1.0}' > "$REWARD_FILE"
    exit 0
else
    echo "FAIL: expected 12, got $RESULT"
    echo '{"score": 0.0}' > "$REWARD_FILE"
    exit 1
fi
