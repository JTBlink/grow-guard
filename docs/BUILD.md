# GrowGuard 构建与打包

用 `scripts/build.sh` 一键构建整个产品(桌面 App + Python 后端),并可打成安装包。

---

## 一、前置工具链(仅开发机需要)

| 工具 | 用途 | 安装 |
|------|------|------|
| Rust + cargo | 编译 Tauri Rust 层 | `curl https://sh.rustup.rs -sSf \| sh` |
| tauri-cli | Tauri 构建 | `cargo install tauri-cli --locked` |
| Node + pnpm | 前端构建 | `brew install node && npm i -g pnpm` |
| Xcode CLT | pkgbuild/hdiutil | `xcode-select --install` |

> 这些是**构建期**依赖,终端设备(装 GrowGuard 的电脑)不需要。
> 若 crates.io 下载慢,可在 `~/.cargo/config.toml` 配国内镜像(如 USTC)。

---

## 二、构建命令

```bash
cd tools/security/grow-guard

scripts/build.sh              # 默认:构建 .app + 组装 dist/ 分发包
scripts/build.sh --pkg        # 额外生成完整安装包 dist/GrowGuard-<版本>.pkg
scripts/build.sh --dmg        # 额外生成 .dmg(依赖 hdiutil,偶发失败)
scripts/build.sh --app-only   # 只构建 .app,不组装 dist/
scripts/build.sh --clean      # 先清理旧产物再构建
```

组合示例:

```bash
scripts/build.sh --clean --pkg    # 干净重建 + 出安装包
```

---

## 三、产物说明

| 产物 | 路径 | 用途 |
|------|------|------|
| 桌面 App | `desktop/src-tauri/target/release/bundle/macos/GrowGuard.app` | 直接运行 |
| 分发文件夹 | `dist/GrowGuard/` | App + backend + CLI + install.sh,可整体拷贝分发 |
| **安装包** | `dist/GrowGuard-<版本>.pkg` | 双击引导安装(推荐分发形态) |
| DMG(可选) | `desktop/.../bundle/dmg/*.dmg` | 拖拽安装(仅 GUI) |

---

## 四、`.pkg` 安装包内容

`--pkg` 生成的安装包会把内容装到**统一目录**:

```
/Applications/GrowGuard.app                          # 桌面 App
/Library/Application Support/GrowGuard/
├── grow-guard.sh
├── backend/*.py
└── scripts/install.sh
# postinstall 自动软链: /usr/local/bin/grow-guard -> grow-guard.sh
```

> pkg 的 postinstall **只软链 CLI**,不自动启守护进程(启守护需家长密码,交互式)。
> 用户装完后运行 `sudo grow-guard install` 起守护。

---

## 五、开发调试(不打包)

```bash
cd desktop
pnpm install
pnpm tauri dev        # 热重载开发桌面 App
pnpm tauri build      # 手动构建(等价 build.sh 内部调用)
```

后端 CLI 单独调试:

```bash
python3 backend/cli.py status --json
python3 backend/cli.py list-apps --json
GROW_GUARD_HOME=/tmp/gg python3 backend/cli.py status   # 用临时数据目录
```

---

## 六、签名与公证(对外分发时)

当前 `.pkg` / `.app` **未签名**,仅适合本地/内部分发。对外分发需:

```bash
# App 签名
codesign --deep --force --sign "Developer ID Application: <你>" GrowGuard.app
# pkg 签名
productsign --sign "Developer ID Installer: <你>" GrowGuard.pkg GrowGuard-signed.pkg
# 公证
xcrun notarytool submit GrowGuard-signed.pkg --keychain-profile <profile> --wait
```

> 需要 Apple Developer 账号 + Developer ID 证书。未签名包用户端会被 Gatekeeper 拦(右键→打开可绕过)。

---

## 七、GitHub 自动发布(CI)

仓库已配置 `.github/workflows/release.yml`,**推送 `v*` 标签**即自动在 GitHub Actions(macOS runner)上构建并发布 Release。

### 发布流程

```bash
# 1. 改版本号(tauri.conf.json 是 CI 校验基准)
#    desktop/src-tauri/tauri.conf.json  ->  "version": "1.0.0"

# 2. 提交后打 tag 推送(tag 号必须 = tauri.conf.json 的 version,否则 CI 报错中止)
git commit -am "release: v1.0.0"
git tag v1.0.0
git push origin main --tags
```

推送后 Actions 依次:校验版本一致 -> 装 Rust/tauri-cli/pnpm -> `scripts/build.sh --pkg` -> 归集产物 -> 创建 Release 并挂附件。

### 发布产物

| 附件 | 说明 |
|------|------|
| `GrowGuard-<版本>.pkg` | 引导安装包(App + CLI + 后端),**推荐分发** |
| `GrowGuard-<版本>.dmg` | 仅桌面 App(中文产品名产物已重命名为 ASCII) |
| `GrowGuard-<版本>-app.zip` | `.app` 压缩包(dmg 兜底) |

### 说明

- **未签名**:CI 不做代码签名/公证,用户端首次打开需右键->打开绕过 Gatekeeper(Release 说明里已注明)。将来接入 Developer ID 时,把证书/密码存 GitHub Secrets,在 build 步骤后追加签名步骤即可。
- **预发布**:tag 带 `-`(如 `v1.0.0-rc1`)会自动标记为 prerelease。
- **重发**:删除同名 tag 重推可覆盖(`git push origin :refs/tags/v1.0.0` 删远端 tag 后重打)。

---

## 八、已知问题

| 问题 | 说明 |
|------|------|
| `--dmg` 偶发失败 | `hdiutil`/`bundle_dmg.sh` 有时报错(卷未卸载等);默认不打 DMG,重试或用 `--pkg` |
| pkg 载荷有 `._*` | 源自 OS 保护的 `com.apple.provenance` xattr,无法剥离;macOS Installer 安装时自动丢弃,无害 |
| 首次 Rust 编译慢 | tauri-cli 依赖 ~400 crates,首次编译 10-15 分钟;后续增量快 |
