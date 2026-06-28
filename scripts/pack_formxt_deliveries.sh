#!/usr/bin/env bash
# 打包 formxt 三本 Agent 交付包
set -euo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
bash "${DIR}/pack_agent_bundle.sh" JESD223E "JESD223E"
bash "${DIR}/pack_agent_bundle.sh" MIPI_MPHY_v5_0 "MIPI M-PHY Specification v5.0"
bash "${DIR}/pack_agent_bundle.sh" MIPI_UniPro_v2_0 "MIPI UniPro Specification v2.0"
echo ""
echo "全部打包完成 → deliveries/"
