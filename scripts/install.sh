#!/bin/bash
# grow-guard 安装/卸载脚本
#
# 职责:
#   install   —— 注册 root LaunchDaemon(KeepAlive 自拉起=防 kill)、初始化配置目录、
#                加载 PF anchor、锁定文件权限(防卸载/防篡改)
#   uninstall —— 卸载守护进程、清除 hosts/PF 规则、放开权限
#
# 防卸载设计:
#   1. LaunchDaemon 以 root 运行且 KeepAlive=true,普通用户 kill 后立即自拉起
#   2. 配置/密钥目录属主 root、密钥 0600,普通用户无法读改
#   3. plist 属主 root:wheel 0644,普通用户无法删除(在 /Library/LaunchDaemons)
#   4. CLI uninstall 需家长密码(见 grow_guard.py cmd_uninstall)
#
# 需 root 运行。

set -euo pipefail

# --- 解析脚本真实路径(支持软链接调用)---
SCRIPT_PATH="${BASH_SOURCE[0]}"
while [ -L "$SCRIPT_PATH" ]; do
    SCRIPT_DIR="$(cd "$(dirname "$SCRIPT_PATH")" && pwd)"
    SCRIPT_PATH="$(readlink "$SCRIPT_PATH")"
    [[ "$SCRIPT_PATH" != /* ]] && SCRIPT_PATH="$SCRIPT_DIR/$SCRIPT_PATH"
done
INSTALL_DIR="$(cd "$(dirname "$SCRIPT_PATH")" && pwd)"   # grow-guard/scripts
TOOL_DIR="$(cd "$INSTALL_DIR/.." && pwd)"                # grow-guard/
SRC_DIR="$TOOL_DIR/backend"

# --- 常量 ---
LABEL="com.jtstudio.grow-guard"
PLIST_PATH="/Library/LaunchDaemons/${LABEL}.plist"
# 统一收拢到 macOS 官方推荐的 App 支持目录(root 独占,普通用户不可写)
APP_SUPPORT="/Library/Application Support/GrowGuard"
GUARD_HOME="$APP_SUPPORT/data"
# 守护进程实际执行的代码副本 —— root 独占,普通用户不可写。
# 关键:绝不让 root LaunchDaemon 直接跑用户目录(源码树)里的 .py,
# 否则改一行源码就能拿到 root。安装时把 backend/ 拷到这里并锁死权限。
LIBEXEC_DIR="$APP_SUPPORT/backend"
GUARD_ENTRY="${LIBEXEC_DIR}/cli.py"
BIN_LINK="/usr/local/bin/grow-guard"
PF_ANCHOR_FILE="/etc/pf.anchors/grow-guard"
PF_CONF="/etc/pf.conf"
PF_ANCHOR_MARK="# grow-guard anchor"
PYTHON_BIN="$(command -v python3 || echo /usr/bin/python3)"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'; NC='\033[0m'
ok()   { echo -e "${GREEN}✓${NC} $1"; }
warn() { echo -e "${YELLOW}!${NC} $1"; }
err()  { echo -e "${RED}✗${NC} $1" >&2; }
info() { echo -e "${CYAN}ℹ${NC} $1"; }

require_root() {
    if [ "$(id -u)" -ne 0 ]; then
        err "请用 sudo 运行: sudo grow-guard install"
        exit 1
    fi
}

# ---------------------------------------------------------------------------
# 核心依赖自检:python3 必需(守护进程与 CLI 都靠它),缺失则中止
# ---------------------------------------------------------------------------
check_core_deps() {
    if [ ! -x "$PYTHON_BIN" ] && ! command -v python3 >/dev/null 2>&1; then
        err "未找到 python3。请先安装(如 'brew install python3')后重试"
        exit 1
    fi
    ok "python3: $PYTHON_BIN"
}

# ---------------------------------------------------------------------------
# PF 防火墙:在 /etc/pf.conf 里挂载 grow-guard anchor
# ---------------------------------------------------------------------------
setup_pf_anchor() {
    # 确保 anchor 文件存在(内容由守护进程动态填充)
    mkdir -p "$(dirname "$PF_ANCHOR_FILE")"
    [ -f "$PF_ANCHOR_FILE" ] || echo "" > "$PF_ANCHOR_FILE"

    # 在 pf.conf 里声明 anchor(只加一次)
    if ! grep -q "$PF_ANCHOR_MARK" "$PF_CONF" 2>/dev/null; then
        {
            echo ""
            echo "$PF_ANCHOR_MARK"
            echo 'anchor "grow-guard"'
            echo 'load anchor "grow-guard" from "/etc/pf.anchors/grow-guard"'
        } >> "$PF_CONF"
        ok "已在 $PF_CONF 挂载 grow-guard PF anchor"
    fi
    # 启用 PF(可能已启用,忽略错误)
    pfctl -f "$PF_CONF" 2>/dev/null || true
    pfctl -E 2>/dev/null || true
}

remove_pf_anchor() {
    if [ -f "$PF_CONF" ] && grep -q "$PF_ANCHOR_MARK" "$PF_CONF" 2>/dev/null; then
        # 删除 grow-guard 相关的 4 行区块
        /usr/bin/python3 - "$PF_CONF" <<'PYEOF'
import sys, re
path = sys.argv[1]
text = open(path).read()
lines = text.splitlines()
out, skip = [], 0
for i, ln in enumerate(lines):
    if ln.strip() == "# grow-guard anchor":
        skip = 3  # 跳过本行 + anchor + load 两行
        continue
    if skip > 0:
        skip -= 1
        continue
    out.append(ln)
open(path, "w").write("\n".join(out).rstrip("\n") + "\n")
PYEOF
        pfctl -f "$PF_CONF" 2>/dev/null || true
    fi
    # 清空 anchor 规则
    [ -f "$PF_ANCHOR_FILE" ] && echo "" > "$PF_ANCHOR_FILE"
    pfctl -a grow-guard -F rules 2>/dev/null || true
    rm -f "$PF_ANCHOR_FILE"
    ok "已移除 PF anchor"
}

# ---------------------------------------------------------------------------
# LaunchDaemon plist
# ---------------------------------------------------------------------------
write_plist() {
    cat > "$PLIST_PATH" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>${LABEL}</string>
    <key>ProgramArguments</key>
    <array>
        <string>${PYTHON_BIN}</string>
        <string>${GUARD_ENTRY}</string>
        <string>daemon</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>ThrottleInterval</key>
    <integer>5</integer>
    <key>StandardOutPath</key>
    <string>${GUARD_HOME}/daemon.out.log</string>
    <key>StandardErrorPath</key>
    <string>${GUARD_HOME}/daemon.err.log</string>
    <key>ProcessType</key>
    <string>Background</string>
</dict>
</plist>
EOF
    chown root:wheel "$PLIST_PATH"
    chmod 0644 "$PLIST_PATH"
    ok "已写入 LaunchDaemon: $PLIST_PATH"
}

install_code() {
    # 代码 + 入口脚本 + 安装脚本都装进统一目录,再软链 CLI 到 PATH。
    # 装 install.sh 是为了让已安装的 cli.py 能自举卸载/重装(它按 parents[1]/scripts 找)。
    # 卸载只需删这一个目录 + 软链 + plist,干净利落。
    mkdir -p "$LIBEXEC_DIR" "$APP_SUPPORT/scripts"
    rm -f "$LIBEXEC_DIR"/*.py
    cp "$SRC_DIR"/*.py "$LIBEXEC_DIR"/
    cp "$TOOL_DIR/grow-guard.sh" "$APP_SUPPORT/grow-guard.sh"
    cp "$INSTALL_DIR"/*.sh "$APP_SUPPORT/scripts/"
    chown -R root:wheel "$APP_SUPPORT"
    chmod 0755 "$LIBEXEC_DIR" "$APP_SUPPORT/grow-guard.sh"
    chmod 0644 "$LIBEXEC_DIR"/*.py
    chmod 0755 "$APP_SUPPORT/scripts"/*.sh
    mkdir -p "$(dirname "$BIN_LINK")"
    ln -sf "$APP_SUPPORT/grow-guard.sh" "$BIN_LINK"
    ok "已将守护代码安装到 root 独占目录: $LIBEXEC_DIR"
    ok "已软链 CLI 到 PATH: $BIN_LINK"
}

lock_permissions() {
    # 目录 root 属主;密钥/密码 0600 —— 普通用户读不到,无法伪造配置或爆破密码
    mkdir -p "$GUARD_HOME"
    chown -R root:wheel "$GUARD_HOME"
    chmod 0755 "$GUARD_HOME"
    [ -f "$GUARD_HOME/guard.key" ] && chmod 0600 "$GUARD_HOME/guard.key"
    [ -f "$GUARD_HOME/auth.json" ] && chmod 0600 "$GUARD_HOME/auth.json"
    # config/state 无密码哈希,0644 让非 root 的 status/gui 只读展示
    [ -f "$GUARD_HOME/config.json" ] && chmod 0644 "$GUARD_HOME/config.json"
    [ -f "$GUARD_HOME/state.json" ] && chmod 0644 "$GUARD_HOME/state.json"
    ok "已锁定配置目录权限(root 属主,密码/密钥 0600)"
}

# ---------------------------------------------------------------------------
# install
# ---------------------------------------------------------------------------
do_install() {
    require_root
    info "安装 grow-guard 守护进程..."

    check_core_deps

    if [ ! -f "$GUARD_HOME/config.json" ]; then
        warn "尚未设置主密码。守护进程会空转,直到你运行: sudo grow-guard install(会引导设密码)"
    fi

    mkdir -p "$GUARD_HOME"
    install_code
    setup_pf_anchor
    write_plist
    lock_permissions

    # 加载(先卸再载,幂等)
    launchctl bootout system "$PLIST_PATH" 2>/dev/null || true
    launchctl bootstrap system "$PLIST_PATH" 2>/dev/null || \
        launchctl load -w "$PLIST_PATH"   # 旧系统回退
    ok "守护进程已启动(开机自启 + kill 自拉起)"
    echo
    info "常用命令:"
    echo "  sudo grow-guard limit Safari 60"
    echo "  sudo grow-guard block-site youtube.com"
    echo "  sudo grow-guard schedule --start 07:00 --end 21:30"
    echo "  grow-guard status"
}

# ---------------------------------------------------------------------------
# uninstall
# ---------------------------------------------------------------------------
do_uninstall() {
    require_root
    info "卸载 grow-guard..."

    launchctl bootout system "$PLIST_PATH" 2>/dev/null || \
        launchctl unload -w "$PLIST_PATH" 2>/dev/null || true
    rm -f "$PLIST_PATH"
    ok "已移除 LaunchDaemon"

    # 清除 hosts 托管区块
    if [ -f /etc/hosts ] && grep -q "grow-guard managed block" /etc/hosts; then
        /usr/bin/python3 - <<'PYEOF'
path = "/etc/hosts"
text = open(path).read()
B = "# >>> grow-guard managed block >>>"
E = "# <<< grow-guard managed block <<<"
if B in text and E in text:
    pre = text.split(B)[0].rstrip("\n")
    post = text.split(E)[1].lstrip("\n")
    parts = [p for p in (pre, post) if p]
    open(path, "w").write("\n".join(parts) + "\n")
PYEOF
        dscacheutil -flushcache 2>/dev/null || true
        killall -HUP mDNSResponder 2>/dev/null || true
        ok "已清除 hosts 屏蔽"
    fi

    remove_pf_anchor

    # 统一目录一删干净:代码/数据/入口都在 APP_SUPPORT 下,再摘 PATH 软链
    rm -f "$BIN_LINK"
    rm -rf "$APP_SUPPORT"
    rm -rf "$SRC_DIR/__pycache__"
    ok "已彻底清除守护代码、配置、密码、日志与缓存"
    info "grow-guard 已完全卸载,系统恢复原状"
}

# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------
case "${1:-}" in
    install)   do_install ;;
    uninstall) do_uninstall ;;
    *)
        echo "用法: $0 {install|uninstall}"
        echo "(通常通过 'sudo grow-guard install' / 'sudo grow-guard uninstall' 调用)"
        exit 1
        ;;
esac
