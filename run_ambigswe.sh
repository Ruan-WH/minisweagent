#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec bash "$ROOT/evaluation/benchmarks/swe_bench/scripts/run_mini_swe.sh" "$@"
