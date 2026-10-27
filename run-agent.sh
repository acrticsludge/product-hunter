#!/bin/bash
set -euo pipefail
cd /home/ubuntu/product-hunter
source /home/ubuntu/.local/bin/env

echo "ph-agent started at $(date)" >> /home/ubuntu/product-hunter/agent.log

# Run the autonomy engine (drives research runs through all stages).
# --once runs a single cycle; omit for continuous operation.
exec uv run python -m product_hunter.autonomy >> /home/ubuntu/product-hunter/agent.log 2>&1