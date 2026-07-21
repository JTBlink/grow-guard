#!/usr/bin/env bash
# CI: 校验 tag 版本与 tauri.conf.json 的 version 一致(防止漏改版本号)。
# 仅在 tag 触发时由 workflow 调用;手动触发(workflow_dispatch)不执行本脚本。
# 依赖环境变量: GITHUB_REF_NAME(如 v1.0.0),由 GitHub Actions 注入。
set -euo pipefail

TAG="${GITHUB_REF_NAME#v}"
CONF_VER="$(grep -m1 '"version"' desktop/src-tauri/tauri.conf.json | sed -E 's/.*"([0-9.]+)".*/\1/')"
echo "tag=$TAG  tauri.conf.json=$CONF_VER"

if [ "$TAG" != "$CONF_VER" ]; then
    echo "::error::标签 v$TAG 与 tauri.conf.json 的 version($CONF_VER)不一致,请先同步版本号"
    exit 1
fi
