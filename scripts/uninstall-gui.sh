#!/bin/bash
set -euo pipefail

SCRIPT_PATH="${BASH_SOURCE[0]}"
while [ -L "$SCRIPT_PATH" ]; do
    SCRIPT_DIR="$(cd "$(dirname "$SCRIPT_PATH")" && pwd)"
    SCRIPT_PATH="$(readlink "$SCRIPT_PATH")"
    [[ "$SCRIPT_PATH" != /* ]] && SCRIPT_PATH="$SCRIPT_DIR/$SCRIPT_PATH"
done
INSTALL_DIR="$(cd "$(dirname "$SCRIPT_PATH")" && pwd)"
TOOL_DIR="$(cd "$INSTALL_DIR/.." && pwd)"

APP_SUPPORT="/Library/Application Support/GrowGuard"
GUI_APP="/Applications/青锁盾.app"
GUI_BIN="$GUI_APP/Contents/MacOS/青锁盾"
BIN_LINK="/usr/local/bin/grow-guard"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'; NC='\033[0m'
ok()   { echo -e "${GREEN}✓${NC} $1"; }
warn() { echo -e "${YELLOW}!${NC} $1"; }
err()  { echo -e "${RED}✗${NC} $1" >&2; }
info() { echo -e "${CYAN}ℹ${NC} $1"; }

require_root() {
    if [ "$(id -u)" -ne 0 ]; then
        err "请用 sudo 运行: sudo bash scripts/force-uninstall.sh"
        exit 1
    fi
}

main() {
    require_root
    info "强制卸载青锁盾..."

    if [ -x "$TOOL_DIR/scripts/install.sh" ]; then
        /bin/bash "$TOOL_DIR/scripts/install.sh" uninstall
    else
        warn "未找到 install.sh，跳过守护/配置清理"
    fi

    pkill -9 -f "$GUI_BIN" 2>/dev/null || true
    rm -rf "$GUI_APP"
    rm -f "$BIN_LINK"
    rm -rf "$APP_SUPPORT"

    ok "已强制移除桌面 App、CLI 软链与安装目录"
}

main "$@"
