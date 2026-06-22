#!/usr/bin/env bash
# Auctorum warm-up cron — keep nomic-embed and llava-phi3 hot in Ollama
# RAM so first-call latency stays sub-2s instead of 4-6s cold.
#
# Install (already done by night sweep, kept here for reproducibility):
#
#   sudo mkdir -p /opt/keewarm && sudo chown $USER /opt/keewarm
#   cp warm_cron.sh /opt/keewarm/warm.sh
#   chmod +x /opt/keewarm/warm.sh
#   ( crontab -l 2>/dev/null | grep -v warm.sh ; \
#     echo "*/25 * * * * /opt/keewarm/warm.sh" ) | crontab -
#
# OLLAMA_KEEP_ALIVE=24h is set in the systemd override, but each model
# evicts after that window. Pinging every 25 min keeps both resident.

set -e
LOG=/opt/keewarm/warm.log
{
  echo "[$(date -Iseconds)] warming nomic-embed-text..."
  curl -s -X POST http://127.0.0.1:11434/api/embed \
    -H "Content-Type: application/json" \
    -d '{"model":"nomic-embed-text","input":"warm"}' >/dev/null \
    && echo "  nomic OK" || echo "  nomic FAIL"

  echo "[$(date -Iseconds)] warming llava-phi3:3.8b..."
  curl -s -X POST http://127.0.0.1:11434/api/generate \
    -H "Content-Type: application/json" \
    -d '{"model":"llava-phi3:3.8b","prompt":"hi","stream":false,"options":{"num_predict":1}}' >/dev/null \
    && echo "  llava OK" || echo "  llava FAIL"
} >> "$LOG" 2>&1
