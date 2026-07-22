#!/usr/bin/env bash
# CI: 归集构建产物到 release-artifacts/,统一改成 ASCII 文件名便于下载
# (产品名含中文"青锁盾",dmg/app 产物名需重命名)。
#
# 版本号来源:
#   - tag 触发   -> 取自 GITHUB_REF_NAME(去掉前缀 v)
#   - 手动触发   -> 取自 desktop/src-tauri/tauri.conf.json 的 version
#
# 依赖环境变量(GitHub Actions 注入):
#   GITHUB_REF        判断是否 tag 触发(refs/tags/*)
#   GITHUB_REF_NAME   tag 名
#   GITHUB_OUTPUT     写出 version=<版本> 供后续步骤引用
set -euo pipefail

if [[ "${GITHUB_REF:-}" == refs/tags/* ]]; then
    VERSION="${GITHUB_REF_NAME#v}"
else
    VERSION="$(grep -m1 '"version"' desktop/src-tauri/tauri.conf.json | sed -E 's/.*"([0-9.]+)".*/\1/')"
fi

OUT="release-artifacts"
mkdir -p "$OUT"
# 通用二进制构建产物路径(与 build.sh 默认 TARGET 一致)
BUNDLE="desktop/src-tauri/target/universal-apple-darwin/release/bundle"

# .pkg(build.sh 已按 ASCII 命名 GrowGuard-<版本>.pkg)
if [ -f "dist/GrowGuard-${VERSION}.pkg" ]; then
    cp "dist/GrowGuard-${VERSION}.pkg" "$OUT/GrowGuard-${VERSION}.pkg"
fi

# .dmg(产品名为中文,重命名为 ASCII)
dmg="$(find "$BUNDLE/dmg" -maxdepth 1 -name '*.dmg' 2>/dev/null | head -1 || true)"
if [ -n "$dmg" ]; then
    cp "$dmg" "$OUT/GrowGuard-${VERSION}.dmg"
fi

# .app 打成 zip 兜底(dmg 偶发失败时仍有可分发的 App)
app="$(find "$BUNDLE/macos" -maxdepth 1 -name '*.app' 2>/dev/null | head -1 || true)"
if [ -n "$app" ]; then
    ditto -c -k --sequesterRsrc --keepParent "$app" "$OUT/GrowGuard-${VERSION}-app.zip"
fi

# 供后续步骤(Release)引用;GITHUB_OUTPUT 不存在时(本地测试)退化为打印
if [ -n "${GITHUB_OUTPUT:-}" ]; then
    echo "version=$VERSION" >> "$GITHUB_OUTPUT"
fi
echo "生成的发布产物(版本 $VERSION):"
ls -lh "$OUT"
