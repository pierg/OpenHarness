#!/usr/bin/env bash
set -euo pipefail

RESULT=$(python /app/sum_evens.py)

if [ "$RESULT" = "12" ]; then
    echo "PASS: sum_evens([1,2,3,4,5,6]) = 12"
    exit 0
else
    echo "FAIL: expected 12, got $RESULT"
    exit 1
fi
