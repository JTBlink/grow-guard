# GrowGuard 安装与卸载

GrowGuard 有两部分：**桌面 App**(图形界面)和 **root 守护进程 + CLI**(实际执行限制)。
所有已安装内容统一在 `/Library/Application Support/GrowGuard/`,卸载一删干净。

---

## 一、用 .pkg 安装包(推荐,一次装全)

`.pkg` 会自动把 App 装到 `/Applications`、代码/CLI 装到统一目录、并软链 `grow-guard` 到 PATH。

```bash
# 双击 dist/GrowGuard-<版本>.pkg,或命令行:
sudo installer -pkg dist/GrowGuard-0.1.0.pkg -target /
```

装完后**还需启用家长控制守护进程**(需家长密码,pkg 不自动跑):

```bash
sudo grow-guard install      # 首次会引导设置家长主密码 + 起守护(开机自启)
```

> 未签名的 pkg 首次打开会被 Gatekeeper 拦:右键 → 打开,或系统设置 → 隐私与安全性 → 仍要打开。

---

## 二、命令行直接安装(开发/脚本化)

不装 App,只要 CLI + 守护进程:

```bash
# 在仓库内
sudo tools/security/grow-guard/grow-guard.sh install
# 或注册到 PATH 后
sudo grow-guard install
```

`install` 会:
1. 校验依赖(python3 必需)
2. 把 `backend/*.py` + 入口脚本拷到 `/Library/Application Support/GrowGuard/`(root 独占)
3. 软链 `grow-guard` 到 `/usr/local/bin`
4. 写 LaunchDaemon plist(`KeepAlive` 自拉起)+ 挂 PF anchor + 锁权限
5. 首次引导设置家长主密码

---

## 三、常用配置

```bash
sudo grow-guard limit Safari 60                 # Safari 每日 60 分钟
sudo grow-guard lock-app "Game Center"          # 直接禁用
sudo grow-guard block-site youtube.com          # 屏蔽网站
sudo grow-guard schedule --start 07:00 --end 21:30
grow-guard status                               # 只读,无需密码
grow-guard app                                  # 打开桌面 App
```

---

## 四、卸载(彻底清除)

```bash
sudo grow-guard uninstall      # 需家长密码验证
```

卸载会**一次清干净**:
- `/Library/Application Support/GrowGuard/`(代码 + 配置 + 密码 + 密钥 + 日志)
- `/usr/local/bin/grow-guard`(PATH 软链)
- `/Library/LaunchDaemons/com.jtstudio.grow-guard.plist`
- `/etc/hosts` 托管区块 + `/etc/pf.anchors/grow-guard` + `/etc/pf.conf` anchor 声明

桌面 App 单独删:

```bash
sudo rm -rf /Applications/GrowGuard.app
```

---

## 五、安装后的系统布局

```
/Applications/GrowGuard.app                          # 桌面 App
/Library/Application Support/GrowGuard/               # 统一目录(root 独占)
├── grow-guard.sh                                     # CLI 入口
├── backend/                                          # 守护代码副本
└── data/  (config.json / state.json / auth.json / guard.key / guard.log)
/usr/local/bin/grow-guard                             # -> grow-guard.sh
/Library/LaunchDaemons/com.jtstudio.grow-guard.plist  # 开机自启
```

---

## 六、故障排查

| 现象 | 处理 |
|------|------|
| `status` 报权限/篡改 | 守护以 root 运行;非 root 只读展示。配置被改会 fail-closed 继续锁定 |
| `grant-fda` 后用量仍为估算 | 在系统设置 → 隐私 → 完全磁盘访问 里手动勾选运行程序 |
| 卸载后残留 | `sudo rm -rf "/Library/Application Support/GrowGuard" /usr/local/bin/grow-guard` |
