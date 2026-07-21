#!/bin/bash
# GrowGuard 产品打包脚本
#
# 一键构建整个产品:
#   1. 校验工具链(cargo / tauri-cli / pnpm)
#   2. 前端依赖安装 + Tauri 构建(React → Rust → .app / .dmg)
#   3. 汇总产物到 dist/:桌面 App + Python backend + 安装脚本 + 文档
#
# 用法:
#   scripts/build.sh              # 完整打包(桌面 App + backend 分发包)
#   scripts/build.sh --app-only   # 只构建桌面 App,不组装分发包
#   scripts/build.sh --clean      # 先清理旧产物再构建

set -euo pipefail

# --- 解析脚本真实路径(支持软链接)---
SCRIPT_PATH="${BASH_SOURCE[0]}"
while [ -L "$SCRIPT_PATH" ]; do
    SCRIPT_DIR="$(cd "$(dirname "$SCRIPT_PATH")" && pwd)"
    SCRIPT_PATH="$(readlink "$SCRIPT_PATH")"
    [[ "$SCRIPT_PATH" != /* ]] && SCRIPT_PATH="$SCRIPT_DIR/$SCRIPT_PATH"
done
SCRIPTS_DIR="$(cd "$(dirname "$SCRIPT_PATH")" && pwd)"
TOOL_DIR="$(cd "$SCRIPTS_DIR/.." && pwd)"          # grow-guard/
DESKTOP_DIR="$TOOL_DIR/desktop"
BACKEND_DIR="$TOOL_DIR/backend"
DIST_DIR="$TOOL_DIR/dist"
BUNDLE_DIR="$DESKTOP_DIR/src-tauri/target/release/bundle"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'; NC='\033[0m'
ok()   { echo -e "${GREEN}✓${NC} $1"; }
warn() { echo -e "${YELLOW}!${NC} $1"; }
err()  { echo -e "${RED}✗${NC} $1" >&2; }
info() { echo -e "${CYAN}ℹ${NC} $1"; }

APP_ONLY=0
DO_CLEAN=0
MAKE_DMG=1
MAKE_PKG=0
for arg in "$@"; do
    case "$arg" in
        --app-only) APP_ONLY=1; MAKE_DMG=0 ;;
        --clean)    DO_CLEAN=1 ;;
        --no-dmg)   MAKE_DMG=0 ;;
        --pkg)      MAKE_PKG=1 ;;
        -h|--help)
            echo "用法: $0 [--app-only] [--no-dmg] [--pkg] [--clean]"
            echo "  默认:构建 .app + 打 .dmg + 组装 dist/"
            echo "  --no-dmg   跳过 DMG(仅 .app + dist/)"
            echo "  --pkg      额外生成 .pkg 引导安装包"
            echo "  --app-only 只构建 .app,不打 dmg、不组装 dist/"
            exit 0 ;;
        *) err "未知参数: $arg"; exit 1 ;;
    esac
done

VERSION="$(grep -m1 '"version"' "$DESKTOP_DIR/src-tauri/tauri.conf.json" | sed -E 's/.*"([0-9.]+)".*/\1/')"
VERSION="${VERSION:-0.1.0}"

# --- 工具链自检 ---
# cargo 可能只在 ~/.cargo/env 里,先尝试加载
[ -f "$HOME/.cargo/env" ] && . "$HOME/.cargo/env"
export PATH="$HOME/.cargo/bin:$PATH"

check_toolchain() {
    local missing=0
    for tool in cargo pnpm; do
        if ! command -v "$tool" >/dev/null 2>&1; then
            err "缺少 $tool"
            missing=1
        fi
    done
    if ! cargo tauri --version >/dev/null 2>&1; then
        err "缺少 tauri-cli(安装: cargo install tauri-cli --locked)"
        missing=1
    fi
    if [ "$missing" -ne 0 ]; then
        err "工具链不完整,无法打包桌面 App。请先安装 Rust + tauri-cli + pnpm"
        exit 1
    fi
    ok "工具链就绪: cargo $(cargo --version | awk '{print $2}'), tauri $(cargo tauri --version | awk '{print $2}'), pnpm $(pnpm --version)"
}

build_desktop() {
    cd "$DESKTOP_DIR"
    if [ ! -d node_modules ]; then
        info "安装前端依赖(pnpm install)..."
        pnpm install
    fi
    if [ "$MAKE_DMG" -eq 1 ]; then
        info "构建桌面 App + DMG(React → Rust → .app / .dmg)..."
        # 先清理可能的残留挂载/中间 dmg,降低 hdiutil 偶发失败
        hdiutil detach "/Volumes/青锁盾" 2>/dev/null || true
        find "$BUNDLE_DIR/macos" -maxdepth 1 -name 'rw.*.dmg' -delete 2>/dev/null || true
        cargo tauri build
    else
        info "构建桌面 App(React → Rust → .app)..."
        cargo tauri build --bundles app
    fi
    ok "桌面 App 构建完成"
}

assemble_dist() {
    info "组装分发包到 dist/ ..."
    rm -rf "$DIST_DIR"
    mkdir -p "$DIST_DIR/GrowGuard/backend" "$DIST_DIR/GrowGuard/scripts"

    # 桌面 App 产物(.app / .dmg)
    if [ -d "$BUNDLE_DIR/macos" ]; then
        cp -R "$BUNDLE_DIR/macos/"*.app "$DIST_DIR/GrowGuard/" 2>/dev/null || true
    fi
    if [ -d "$BUNDLE_DIR/dmg" ]; then
        cp "$BUNDLE_DIR/dmg/"*.dmg "$DIST_DIR/" 2>/dev/null || true
    fi

    # Python 后端 + 入口 + 安装脚本 + 文档(用于命令行安装守护进程)
    cp "$BACKEND_DIR"/*.py         "$DIST_DIR/GrowGuard/backend/"
    cp "$SCRIPTS_DIR/install.sh"    "$DIST_DIR/GrowGuard/scripts/"
    cp "$TOOL_DIR/grow-guard.sh"    "$DIST_DIR/GrowGuard/"
    [ -f "$TOOL_DIR/README.md" ] && cp "$TOOL_DIR/README.md" "$DIST_DIR/GrowGuard/"

    ok "分发包已组装: $DIST_DIR/"
}

make_pkg() {
    command -v pkgbuild >/dev/null && command -v productbuild >/dev/null || {
        err "缺少 pkgbuild/productbuild(Xcode CLT),无法生成 .pkg"; return 1;
    }
    # 动态发现 .app 名(productName 可能是中文,如"青锁盾.app"),不硬编码
    local app_src; app_src="$(find "$BUNDLE_DIR/macos" -maxdepth 1 -name '*.app' 2>/dev/null | head -1)"
    [ -n "$app_src" ] && [ -d "$app_src" ] || { err "未找到 .app,先构建桌面 App"; return 1; }

    info "生成 .pkg 安装包..."
    local work; work="$(mktemp -d)"
    local root="$work/root"
    local scripts="$work/scripts"
    local appsup="$root/Library/Application Support/GrowGuard"
    mkdir -p "$root/Applications" "$appsup/backend" "$appsup/scripts" "$scripts"

    # 载荷:App -> /Applications;后端+脚本+入口统一 -> /Library/Application Support/GrowGuard
    # (COPYFILE_DISABLE 减少 AppleDouble;残留的 ._* 源自 com.apple.provenance,
    #  macOS Installer 安装时会自动丢弃,不影响真实文件落地)
    COPYFILE_DISABLE=1 cp -R "$app_src"             "$root/Applications/"
    COPYFILE_DISABLE=1 cp "$BACKEND_DIR"/*.py       "$appsup/backend/"
    COPYFILE_DISABLE=1 cp "$SCRIPTS_DIR/install.sh"  "$appsup/scripts/"
    COPYFILE_DISABLE=1 cp "$TOOL_DIR/grow-guard.sh"  "$appsup/"
    chmod +x "$appsup/grow-guard.sh"

    # postinstall:把 CLI 软链到 PATH(启守护需家长密码,不在此自动跑)
    cat > "$scripts/postinstall" <<'POST'
#!/bin/bash
APPSUP="/Library/Application Support/GrowGuard"
mkdir -p /usr/local/bin
ln -sf "$APPSUP/grow-guard.sh" /usr/local/bin/grow-guard
chmod +x "$APPSUP/grow-guard.sh"
exit 0
POST
    chmod +x "$scripts/postinstall"

    local component="$work/GrowGuard-component.pkg"
    pkgbuild --root "$root" \
             --scripts "$scripts" \
             --identifier "com.jtstudio.growguard" \
             --version "$VERSION" \
             --install-location "/" \
             "$component" >/dev/null

    mkdir -p "$DIST_DIR"
    local out="$DIST_DIR/GrowGuard-${VERSION}.pkg"
    productbuild --package "$component" "$out" >/dev/null
    rm -rf "$work"
    ok "安装包已生成: $out"
}

print_artifacts() {
    echo
    info "产物清单:"
    [ -d "$BUNDLE_DIR/macos" ] && find "$BUNDLE_DIR/macos" -maxdepth 1 -name '*.app' -exec echo "  App:  {}" \;
    [ -d "$BUNDLE_DIR/dmg" ]   && find "$BUNDLE_DIR/dmg"   -maxdepth 1 -name '*.dmg' -exec echo "  DMG:  {}" \;
    [ -f "$DIST_DIR/GrowGuard-${VERSION}.pkg" ] && echo "  安装包: $DIST_DIR/GrowGuard-${VERSION}.pkg"
    if [ "$APP_ONLY" -eq 0 ] && [ -d "$DIST_DIR" ]; then
        echo "  分发包: $DIST_DIR/"
    fi
}

# --- main ---
if [ "$DO_CLEAN" -eq 1 ]; then
    info "清理旧产物..."
    rm -rf "$DIST_DIR" "$DESKTOP_DIR/dist" "$BUNDLE_DIR"
    ok "已清理"
fi

check_toolchain
build_desktop
if [ "$APP_ONLY" -eq 0 ]; then
    assemble_dist
fi
if [ "$MAKE_PKG" -eq 1 ]; then
    make_pkg
fi
print_artifacts
ok "打包完成"
