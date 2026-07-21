#!/bin/bash
# grow-guard 青少年访问锁入口 / 启动器
# 应用限制 + 网站过滤 + 时间管理 + 家长密码 + 防卸载
#
# 用法:
#   grow-guard app            启动桌面 App(优先跑已构建的 App,否则 tauri dev)
#   grow-guard dev            前端开发模式(热重载;会先把改动的 backend 同步到守护进程)
#   grow-guard <子命令> …      直接透传给 CLI(status/limit/install/uninstall/…)
#   grow-guard                无参数时打印帮助
#
# 逻辑主体在 backend/cli.py;桌面 App 在 desktop/。
# 支持通过软链接调用(bin/grow-guard -> tools/security/grow-guard/grow-guard.sh)

SCRIPT_PATH="${BASH_SOURCE[0]}"
while [ -L "$SCRIPT_PATH" ]; do
    SCRIPT_DIR="$(cd "$(dirname "$SCRIPT_PATH")" && pwd)"
    SCRIPT_PATH="$(readlink "$SCRIPT_PATH")"
    [[ "$SCRIPT_PATH" != /* ]] && SCRIPT_PATH="$SCRIPT_DIR/$SCRIPT_PATH"
done
SCRIPT_DIR="$(cd "$(dirname "$SCRIPT_PATH")" && pwd)"

CLI="$SCRIPT_DIR/backend/cli.py"
DESKTOP_DIR="$SCRIPT_DIR/desktop"
BUILT_APP_DIR="$DESKTOP_DIR/src-tauri/target/release/bundle/macos"
DIST_APP_DIR="$SCRIPT_DIR/dist/GrowGuard"
INSTALL_SCRIPT="$SCRIPT_DIR/scripts/install.sh"
INSTALLED_BACKEND="/Library/Application Support/GrowGuard/backend"

# dev 前强制把 backend 全量重装到 root 已安装副本并重启守护进程。
# 守护进程(root)只跑已安装副本,dev 不会自动更新它 —— 故每次 dev 都主动全量重装,
# 确保跑的一定是最新 daemon 代码。install.sh install 幂等:rm 旧 .py + 重拷全部 +
# 重写 plist + bootout/bootstrap 重启;data(config/state/auth/密钥)不动,不丢密码或设置。
sync_daemon() {
    [ -f "$INSTALL_SCRIPT" ] || return 0
    # 从未安装过:首次要走设密码流程,不在 dev 里强装,只提示
    if [ ! -d "$INSTALLED_BACKEND" ]; then
        echo "ℹ 守护进程尚未安装,daemon 侧改动不会生效。首次请: sudo grow-guard install" >&2
        return 0
    fi
    echo "ℹ 正在把最新 backend 全量重装到守护进程并重启(需管理员密码)..." >&2
    sudo /bin/bash "$INSTALL_SCRIPT" install
}

launch_app() {
    # 优先打开已构建的 .app(名字随 productName,可能是中文如"青锁盾.app",故动态查找)
    for dir in "$DIST_APP_DIR" "$BUILT_APP_DIR"; do
        app="$(find "$dir" -maxdepth 1 -name '*.app' 2>/dev/null | head -1)"
        if [ -n "$app" ] && [ -d "$app" ]; then
            exec open "$app"
        fi
    done
    if command -v pnpm >/dev/null 2>&1 && [ -d "$DESKTOP_DIR" ]; then
        echo "未找到已构建的桌面 App,改用开发模式(pnpm tauri dev)..." >&2
        [ -f "$HOME/.cargo/env" ] && . "$HOME/.cargo/env"
        cd "$DESKTOP_DIR" || exit 1
        [ -d node_modules ] || pnpm install
        exec pnpm tauri dev
    fi
    echo "无法启动桌面 App:未构建且缺少 pnpm。请先运行 scripts/build.sh" >&2
    exit 1
}

launch_dev() {
    # 前端开发模式:热重载,改 .tsx/.css 即时刷新窗口
    command -v pnpm >/dev/null 2>&1 || { echo "缺少 pnpm,无法开发模式" >&2; exit 1; }
    sync_daemon
    [ -f "$HOME/.cargo/env" ] && . "$HOME/.cargo/env"
    cd "$DESKTOP_DIR" || exit 1
    [ -d node_modules ] || pnpm install
    exec pnpm tauri dev
}

show_help() {
    cat <<'EOF'
青锁盾 grow-guard —— macOS 青少年访问锁

用法: grow-guard <命令> [参数]

启动:
  app            打开桌面 App(青锁盾.app;未构建则回退开发模式)
  dev            前端开发模式(热重载;自动把改动的 backend 同步到守护进程)

管控(透传 CLI,多数需 sudo):
  install        安装守护进程(首次引导设家长密码)
  uninstall      卸载(需家长密码,彻底清除)
  status         查看状态与今日用量(只读,无需密码)
  limit <应用> <分钟>      设 App 每日时长上限
  lock-app <应用>          直接禁用某 App
  block-site <域名...>     屏蔽网站
  schedule --start HH:MM --end HH:MM   设允许使用时段
  unlock [分钟] / relock   临时解锁 / 立即恢复
  grant-fda      引导授予完全磁盘访问(精确用量)

  完整 CLI 命令: grow-guard cli-help

打包(独立能力,不在此入口):
  scripts/build.sh          构建 .app + .dmg,--pkg 出安装包
EOF
}

case "${1:-}" in
    app|desktop)
        launch_app
        ;;
    dev)
        launch_dev
        ;;
    cli-help)
        exec python3 "$CLI" --help
        ;;
    ""|-h|--help|help)
        show_help
        ;;
    *)
        exec python3 "$CLI" "$@"
        ;;
esac
