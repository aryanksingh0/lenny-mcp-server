#!/bin/bash
# Quick script to check ingestion progress

echo "=== Ingestion Progress ==="
echo ""

# Check if process is running
PID=$(pgrep -f ingest_transcripts_v3.py)
if [ -z "$PID" ]; then
    echo "❌ Ingestion process not running"
    echo ""
    echo "Last output:"
    tail -20 /tmp/ingestion_v3.log
else
    echo "✓ Process running (PID: $PID)"
    echo ""
    echo "Current status:"
    tail -10 /tmp/ingestion_v3.log
fi

echo ""
echo "=== To view full log ==="
echo "tail -f /tmp/ingestion_v3.log"
