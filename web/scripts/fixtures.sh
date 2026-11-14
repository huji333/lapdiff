#!/usr/bin/env bash
# fixtures の 3 ファイルを一時ディレクトリに取り込み、web/public/data/okayama.json に書き出す。
# 使い方（リポジトリのどこからでも）：web/scripts/fixtures.sh
set -euo pipefail
root="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$root"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
for csv in fixtures/racechrono/*.csv; do
  uv run python -m ingest.racechrono "$csv" "$tmp"
done
uv run python -m ingest.acraw fixtures/ac/okayama_mx5nd_lap2.acraw.gz "$tmp"
uv run python -m analysis.export "$tmp" okayama web/public/data
