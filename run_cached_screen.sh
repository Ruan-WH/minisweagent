#!/usr/bin/env bash
set -euo pipefail
PROJECT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
export PYTHONPATH="$PROJECT/vendor/mini-swe-agent/src:$PROJECT"
export MSWEA_GLOBAL_CONFIG_DIR=/tmp/ambig-mini-config
export MSWEA_SILENT_STARTUP=1
export LITELLM_LOCAL_MODEL_COST_MAP=True
unset ALL_PROXY all_proxy HTTP_PROXY http_proxy HTTPS_PROXY https_proxy
cd "$PROJECT"
exec /home/rwh/Ambig-SWE-Origin/.venv-mini/bin/python -B -u \
  -m evaluation.analysis.mini_cached_screen_20261120.run "$@"
