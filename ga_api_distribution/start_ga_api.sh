#!/bin/sh
set -eu
HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
cd "$HERE"
if [ -f ga_api.env ]; then
  # shellcheck disable=SC1091
  . "$HERE/ga_api.env"
fi
if [ ! -f ga_api_config.json ]; then
  echo "缺少 ga_api_config.json，请先复制 ga_api_config.example.json 并由对方 GA 填写。" >&2
  exit 1
fi
exec python3 ga_api_server.py
