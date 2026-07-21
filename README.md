# grow-guard 青少年访问锁

macOS 端的家长控制工具:**应用限制 + 网站过滤 + 时间管理 + 家长密码 + 防卸载**。
不依赖系统 Screen Time,完全命令行控制,一次 `sudo` 安装后由 root 守护进程自动执行。

## 特性

| 能力 | 说明 |
|------|------|
| 🎮 **应用限制** | 按 App 设每日使用时长上限,到点后温和锁定(隐藏到后台,不强杀、不丢数据);也可直接禁用某 App |
| 🌐 **网站过滤** | 域名黑名单,`hosts` + `PF 防火墙` 双层屏蔽(改 hosts 也绕不过 PF) |
| ⏰ **时间管理** | 全局允许使用时间窗,窗外自动锁定所有受限 App |
| 🔑 **家长密码** | 主密码(PBKDF2 哈希)保护所有"放松限制"操作:解锁、改限额、卸载 |
| 🛡️ **防卸载** | root LaunchDaemon(`KeepAlive` 自拉起,kill 立即重启)+ 配置 HMAC 签名(改文件被识别为篡改)+ 卸载需密码 |

## 安全模型(防技术型青少年)

1. **守护进程 root 运行**,`KeepAlive=true` —— 普通用户 `kill` 后 5 秒内自动拉起。
2. **代码 root 独占** —— 安装时把 `backend/*.py` 拷到 `/Library/Application Support/GrowGuard/backend`(root:wheel、非组/他人可写),LaunchDaemon 只执行这里的副本;守护启动时自校验属主/权限,不可信就拒绝运行。**绝不让 root 直接跑用户目录里的源码**(否则改一行源码即可拿 root)。
3. **配置签名化** —— `config.json` / `state.json` 用 HMAC-SHA256 签名,密钥 `guard.key` 属主 root、权限 `0600`,普通用户读不到,无法伪造配置绕过。家长密码 PBKDF2 哈希单独存 `auth.json`(属主 root、`0600`),普通用户读不到,无法离线爆破;`config.json` / `state.json` 保持 `0644` 只读,供非 root 的 `status` / `gui` 展示。
4. **篡改 fail-closed** —— 配置签名不符时,守护进程沿用上一份可信策略**继续锁定**,不会因文件被改而放开限制;`state.json` 被改则用量重置为 0(篡改不获益)。
5. **plist 属主 root:wheel** —— 普通用户删不掉 `/Library/LaunchDaemons` 里的启动项。
6. **卸载需家长密码** —— `grow-guard uninstall` 会先校验密码,成功后清除密码/密钥/代码副本。
7. **PF 防火墙层** —— 网站屏蔽同时写 hosts 和 PF anchor(按 IP block),改 hosts 无效。

> 适用对手:**非 admin 子账户**里"会 kill 进程 / 改配置 / 改 hosts 的技术型青少年"。
> **若孩子本身是本机管理员(有 sudo),本工具非安全级** —— 他可 `launchctl bootout`、`pfctl -d`、读 `guard.key` 伪造签名、改系统时间等。能进恢复模式 / 关 SIP / 抹盘重装的对手同样无法防(那是 Apple Screen Time + MDM 才能覆盖的层级)。真正需要强隔离请给孩子建**标准(非管理员)账户**,或上 MDM。

## 安装到 PATH

本工具已注册进 `tools/lib/script_manager.py`,统一安装后可全局调用 `grow-guard`:

```bash
# 在 dev-tools 根目录
python3 tools/lib/script_manager.py install    # 软链接到 ~/.local/bin/grow-guard
```

也可以不注册,直接用 `tools/security/grow-guard/grow-guard.sh`。

## 快速开始

```bash
# 1. 安装守护进程(首次会引导设置家长主密码 + 开机自启 + 防卸载)
sudo grow-guard install

# 2. 配置限制
sudo grow-guard limit Safari 60                 # Safari 每日限用 60 分钟
sudo grow-guard limit com.tencent.xin 30        # 也可直接给 bundle id
sudo grow-guard lock-app "Game Center"          # 直接禁用某 App
sudo grow-guard block-site youtube.com bilibili.com   # 屏蔽网站
sudo grow-guard schedule --start 07:00 --end 21:30    # 只允许这段时间使用

# 3. 查看状态(只读,无需密码)
grow-guard status

# (可选)引导授予完全磁盘访问,让 status 显示系统精确用量
grow-guard grant-fda
```

## 命令参考

| 命令 | 说明 | 需密码 |
|------|------|:---:|
| `passwd` | 修改家长主密码 | ✓ |
| `install` | 安装守护进程(首次引导设密码) | — (需 root) |
| `uninstall` | 卸载守护进程 | ✓ |
| `limit <app> <分钟>` | 设 App 每日时长上限 | ✓ |
| `lock-app <app> [--unblock]` | 直接禁用 / 解禁某 App | ✓ |
| `unlimit <app>` | 移除某 App 所有限制 | ✓ |
| `block-site <域名...>` | 添加网站黑名单 | ✓ |
| `unblock-site <域名...>` | 移除网站黑名单 | ✓ |
| `schedule --start HH:MM --end HH:MM` | 设允许使用时间窗 | ✓ |
| `schedule --disable` | 关闭时间窗限制 | ✓ |
| `unlock [分钟]` | 临时解锁,暂停所有限制(默认 15 分钟) | ✓ |
| `relock` | 立即结束临时解锁 | ✓ |
| `status` | 查看配置与今日用量 | — |
| `grant-fda` | 引导授予完全磁盘访问(让 status 显示系统精确用量) | — |

`<app>` 可以是:App 名称(`Safari`)、完整路径(`/Applications/Safari.app`)、或 bundle id(`com.apple.Safari`)。

## 目录结构

```
grow-guard/
├── grow-guard.sh              # 产品入口(软链接解析 → backend/cli.py)
├── backend/                   # Python 核心(零第三方依赖)
│   ├── cli.py                 # CLI 主入口(status/list-apps --json、install/limit/…)
│   ├── core.py                # 核心库:配置/签名/密码(auth.json)/时长追踪/网站屏蔽/knowledgeC/App 扫描
│   └── daemon.py              # 守护进程(轮询巡检 + 自校验 + 篡改 fail-closed)
├── desktop/                   # Tauri 桌面端(React + TS 前端 + Rust 桥接)
│   ├── src/                   # React UI
│   ├── src-tauri/             # Rust 桥接层(调用 backend/cli.py)
│   ├── package.json
│   └── vite.config.ts
├── scripts/
│   └── install.sh             # 代码副本 + LaunchDaemon plist + PF anchor + 权限锁定
├── docs/
│   ├── ARCHITECTURE.md        # 技术方案与架构
│   ├── INSTALL.md             # 安装 / 卸载指南
│   └── BUILD.md               # 构建 / 打包指南
├── data/                      # 本地运行数据占位(不入库)
├── README.md
└── .gitignore
```

> 桌面端架构与 Tauri↔CLI 桥接细节见 [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md);
> 安装/卸载见 [`docs/INSTALL.md`](docs/INSTALL.md),构建/打包见 [`docs/BUILD.md`](docs/BUILD.md)。

运行时数据(root 属主):

```
/Library/Application Support/GrowGuard/   # 统一安装目录(root 独占,一删干净)
├── grow-guard.sh             # CLI 入口(软链到 /usr/local/bin/grow-guard)
├── backend/                  # 守护实际执行的代码副本(root:wheel,不可被普通用户改)
└── data/
    ├── config.json           # 主配置:限制/网站/时间窗(HMAC 签名,0644 只读展示)
    ├── state.json            # 今日用量(HMAC 签名,跨天自动重置,0644)
    ├── auth.json             # 家长密码 PBKDF2 哈希(仅 root 可读,0600)
    ├── guard.key             # HMAC 密钥(0600)
    └── guard.log             # 运行日志
/usr/local/bin/grow-guard      # -> 上面的 grow-guard.sh(PATH 入口软链)
/Library/LaunchDaemons/com.jtstudio.grow-guard.plist
/etc/pf.anchors/grow-guard     # PF 屏蔽规则(动态)
/etc/hosts                    # grow-guard 托管区块
```

> 卸载会彻底清除以上全部路径(含代码副本、配置、密码、日志与 `__pycache__` 缓存),系统恢复原状。

## 工作原理

守护进程每 30 秒:
1. 用 `lsappinfo` 探测运行中的受限 App,累加使用时长。
2. 判定是否到达上限 / 被禁用 / 在禁用时段 —— 是则用 AppleScript 把它隐藏到后台并弹通知。
3. 定期重新应用 hosts + PF 屏蔽,防止有人手动清掉。
4. 跨天自动重置用量。
5. 配置签名被篡改时**不放开限制**,沿用上一份可信策略继续锁定(fail-closed)。

**临时解锁**(`unlock N`)给一段宽限期,期间所有 App 与网站限制暂停,到期自动恢复。

**关于系统精确用量(knowledgeC)**:`status` 默认用轮询估算用量(±30s);若家长运行 `grow-guard grant-fda` 授予「完全磁盘访问」,则改读系统 `knowledgeC.db` 显示精确历史用量。这**仅用于展示**,实时拦截始终靠轮询,不依赖 FDA —— 因为 FDA 无法脚本静默授予(Apple 限制),不能作为强制手段。

## 图形面板(GUI)

**桌面 App(Tauri + React)** —— 独立窗口,原生观感:

```bash
cd desktop
pnpm install
pnpm tauri dev        # 开发调试
pnpm tauri build      # 打包 .app / .dmg
```

Tauri 侧不含策略逻辑,只调用 `backend/cli.py`(只读走 `status --json`,放松限制走 osascript 提权)。构建需 Rust + Node 工具链(仅开发机需要,终端设备不需要)。App 内含「应用限制 / 网站 / 时间窗 / 解锁·密码」等面板,「应用限制」会扫描已安装 App 列表,勾选后一键限制或禁用,无需记 bundle id。

## 依赖

- Python 3.8+(全内置库,无第三方依赖)
- macOS 内置:`lsappinfo` `osascript` `pfctl` `dig` `launchctl` `dscacheutil` `open`

## 卸载

```bash
sudo grow-guard uninstall          # 需家长密码;彻底移除守护进程 + 屏蔽规则 + 配置 + 密码 + 缓存
```

卸载会自动清除统一安装目录(`/Library/Application Support/GrowGuard`,含代码/配置/密码/日志)、PATH 软链、hosts/PF 规则与 `__pycache__` 缓存,无需再手动 `rm`。

## 与苹果「屏幕使用时间」的关系

**结论:无法脚本静默联动,这是 Apple 的刻意限制。** grow-guard 是 Screen Time 的独立替代/补充,不读写它的数据。

技术事实(已在 macOS 26 上核实):

| 途径 | 能否脚本自动配 | 说明 |
|------|:---:|------|
| Screen Time 屏幕使用时间 | ❌ | 无公开 CLI/API,数据在受 TCC 保护的 `knowledgeC.db`,只能在系统设置里手动点。系统里也没有 `screentime` 之类命令行工具 |
| 配置描述文件 `.mobileconfig` | ⚠️ 半自动 | 可脚本生成,但 macOS 13+ 起 `profiles` 命令**已不能静默安装**,必须在「系统设置 → 通用 → 设备管理」手动确认。能配网站黑白名单、禁用 App |
| **grow-guard(本工具)** | ✅ | 一次 `sudo` 安装后完全命令行控制,这正是它存在的理由 |

**为什么不依赖 Screen Time**:本机管理员密码无法开启系统 Screen Time(受管/MDM 冲突),且它无法工具化进 dev-tools 流程。grow-guard 用 root 守护进程 + PF + hosts 自建了等效能力,可脚本化、可版本管理。

> 如果哪天需要「系统层兜底」,可另做 `gen-profile` 子命令自动生成 `.mobileconfig`,由家长双击安装一次 —— 但那仍需手动确认,无法全自动。当前版本不含此功能。

