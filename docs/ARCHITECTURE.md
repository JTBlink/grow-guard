# GrowGuard 技术方案与架构

> 本文档记录 GrowGuard 的整体技术选型、目录结构、模块职责与关键数据流。
> 面向维护者;使用说明见根目录 `README.md`。

## 1. 定位

macOS 端家长控制工具:**应用限时 + 网站过滤 + 时间窗 + 家长密码 + 防卸载**。
不依赖系统 Screen Time,一次 `sudo` 安装后由 root 守护进程自动执行。

提供两种交互形态,共享同一 Python 核心:
- **CLI**(`grow-guard`)—— 零依赖,可脚本化,守护进程也走它。
- **桌面 App**(Tauri + React)—— 图形面板,通过调用 CLI 复用全部逻辑。

## 2. 分层架构

```
┌─────────────────────────────────────────────┐
│  desktop/  (Tauri 桌面端)                     │
│  React + TS (前端 UI)                         │
│        │  invoke (IPC)                        │
│  src-tauri/ Rust 桥接层                       │
│        │  spawn: python3 backend/cli.py …     │
│        │  提权: osascript with admin           │
└────────┼─────────────────────────────────────┘
         ▼
┌─────────────────────────────────────────────┐
│  backend/  (Python 核心,零第三方依赖)         │
│  cli.py    命令行入口 + 参数解析 + --json      │
│  core.py   Guard 状态机 / 签名 / 密码 / 网站   │
│            屏蔽 / knowledgeC / App 扫描        │
│  daemon.py root 守护进程(轮询巡检 + 自校验)   │
└────────┼─────────────────────────────────────┘
         ▼
┌─────────────────────────────────────────────┐
│  系统层 (macOS 内置)                           │
│  lsappinfo / osascript / pfctl / hosts /      │
│  launchctl / knowledgeC.db / dig              │
└─────────────────────────────────────────────┘
```

**关键点:GUI 不重复实现任何策略逻辑。** Tauri 侧只做两件事:
1. 展示状态(读 `cli.py status --json`);
2. 触发"放松限制"操作时,经 `osascript ... with administrator privileges` 以 root 跑 `cli.py <子命令>`,家长密码走环境变量 `GROW_GUARD_PW`(不进 argv)。

## 3. 目录结构

```
grow-guard/
├── grow-guard.sh          # 产品入口(软链接解析 → backend/cli.py)
├── backend/               # Python 核心(零依赖)
│   ├── cli.py             # CLI 主入口(含 status/list-apps --json)
│   ├── core.py            # Guard 状态机、HMAC 签名、密码、网站屏蔽、knowledgeC、App 扫描
│   └── daemon.py          # root 守护进程(轮询 + 自校验 + fail-closed)
├── desktop/               # Tauri 桌面端
│   ├── src/               # React + TS 前端
│   ├── src-tauri/         # Rust 桥接层(调用 CLI)
│   │   ├── src/lib.rs     # guard_status / list_apps / guard_admin 命令
│   │   └── tauri.conf.json
│   ├── package.json
│   └── vite.config.ts
├── scripts/               # 生命周期脚本
│   └── install.sh         # 安装/卸载:代码副本 + LaunchDaemon + PF + 权限锁定
├── docs/                  # 本文档
├── data/                  # 本地运行数据占位(不入库)
├── README.md
└── .gitignore
```

运行时数据(root 属主,在系统目录):

```
/Library/Application Support/GrowGuard/   # 统一安装目录(root 独占,一删干净)
├── grow-guard.sh   CLI 入口(软链到 /usr/local/bin/grow-guard)
├── backend/        守护实际执行的代码副本(root:wheel,普通用户不可改)
└── data/
    ├── config.json   限制/网站/时间窗(HMAC 签名,0644)
    ├── state.json    今日用量(HMAC 签名,跨天重置,0644)
    ├── auth.json     家长密码 PBKDF2 哈希(root only,0600)
    ├── guard.key     HMAC 密钥(0600)
    └── guard.log
/usr/local/bin/grow-guard   -> grow-guard.sh(PATH 入口软链)
/Library/LaunchDaemons/com.jtstudio.grow-guard.plist
/etc/pf.anchors/grow-guard
/etc/hosts (托管区块)
```

## 4. 技术选型理由

| 层 | 选型 | 理由 |
|----|------|------|
| 核心 | Python 3 stdlib | 零第三方依赖,macOS 自带;守护/CLI/GUI 共用 |
| 桌面壳 | Tauri (Rust) | 体积小、原生窗口;Rust 侧只做 CLI 桥接,不含策略 |
| 前端 | React + TS | 选卡式面板、App 勾选列表等交互 |
| 提权 | osascript admin | 复用系统原生授权框,不自建 setuid/服务 |
| GUI↔核心 | 调 CLI + `--json` | 复用全部 Python 逻辑,避免双份实现 |

## 5. 安全模型(要点)

1. **代码 root 独占**:安装时把 `backend/*.py` 拷到 `/Library/Application Support/GrowGuard/backend`(root:wheel),LaunchDaemon 只跑副本;守护启动自校验属主/权限,不可信即拒绝运行(防"改源码拿 root")。
2. **密码隔离**:PBKDF2 哈希存 `auth.json`(0600),普通用户读不到,无法离线爆破;`config.json`/`state.json` 保持 0644 供非 root 的 `status`/GUI 只读。
3. **签名防篡改 + fail-closed**:配置/状态 HMAC-SHA256 签名;守护发现篡改时沿用上一份可信策略继续锁定,不放开限制。
4. **卸载需密码**:`uninstall` 先校验家长密码,成功后彻底清除代码副本、配置、密码与缓存。

> 威胁模型:防**非 admin 子账户**里的技术型青少年。若孩子是本机管理员(有 sudo),本工具非安全级。

## 6. Tauri ↔ CLI 命令映射

| Tauri command (Rust) | 调用 | 提权 | 用途 |
|----------------------|------|:---:|------|
| `guard_status` | `cli.py status --json` | 否 | 读状态(前端渲染) |
| `list_apps` | `cli.py list-apps --json` | 否 | 已安装 App 列表(勾选限制) |
| `guard_admin(args, pw)` | `cli.py <args>` | 是(osascript) | limit/lock-app/block-site/unlock/schedule/… |

CLI 路径解析优先级(`lib.rs::cli_path`):`GROW_GUARD_CLI` 环境变量 → 已安装副本 `/Library/Application Support/GrowGuard/backend/cli.py` → 开发期仓库内 `../../backend/cli.py`。

## 7. 构建与运行

```bash
# CLI / 守护(终端设备)
sudo grow-guard install        # 装守护进程(首次引导设密码)
grow-guard status              # 只读查看

# 桌面 App(开发机)
cd desktop
pnpm install
pnpm tauri dev                 # 开发调试
pnpm tauri build               # 打包 .app / .dmg
```

桌面 App 依赖 Rust 工具链 + Node,这是**构建期**依赖,不进终端设备;`scripts/install.sh` 只处理 Python。
