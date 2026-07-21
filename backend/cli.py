#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
grow-guard 青少年访问锁 CLI

功能域:
- 应用限制:按 bundle id 设每日时长上限 / 直接禁用
- 网站过滤:域名黑名单(hosts + PF 双层)
- 时间管理:全局允许使用时间窗
- 家长密码:主密码保护所有解锁/修改操作
- 防卸载:守护进程 root 运行 + 配置签名 + 卸载需密码

安全模型:
- 除 status / list 等只读命令外,所有"放松限制"的操作(解锁、改限额、删规则、
  卸载)都要求家长密码。
- 配置文件 HMAC 签名,手动改文件会被守护进程识别为篡改。
"""

import os
import sys
import shutil
import argparse
import getpass
import subprocess
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import core
from core import Guard, Colors, GuardError

TOOL_DIR = Path(__file__).resolve().parents[1]   # grow-guard/
INSTALL_SCRIPT = TOOL_DIR / "scripts" / "install.sh"


# ---------------------------------------------------------------------------
# 输出helper
# ---------------------------------------------------------------------------
def ok(msg):
    print(f"{Colors.GREEN}✓{Colors.NC} {msg}")


def warn(msg):
    print(f"{Colors.YELLOW}!{Colors.NC} {msg}")


def err(msg):
    print(f"{Colors.RED}✗{Colors.NC} {msg}", file=sys.stderr)


def info(msg):
    print(f"{Colors.CYAN}ℹ{Colors.NC} {msg}")


def is_root() -> bool:
    return os.geteuid() == 0


def require_root_hint():
    """写系统级文件需 root;非 root 时提示用 sudo。"""
    if not is_root():
        warn("此操作需要写入系统级配置,请用 sudo 重新运行:")
        print(f"    sudo {' '.join(['grow-guard'] + sys.argv[1:])}")
        return False
    return True


# ---------------------------------------------------------------------------
# 密码校验闸门
# ---------------------------------------------------------------------------
def require_password(guard: Guard) -> bool:
    """要求输入家长密码;未初始化则拒绝。

    GUI 无 tty,通过环境变量 GROW_GUARD_PW 传入密码(单次非交互校验);
    终端下则回退到 getpass 交互(最多 3 次)。
    """
    if not guard.is_initialized():
        err("尚未安装,请先运行: sudo grow-guard install")
        return False
    env_pw = os.environ.get("GROW_GUARD_PW")
    if env_pw is not None:
        if guard.check_password(env_pw):
            return True
        err("密码错误")
        return False
    for _ in range(3):
        pw = getpass.getpass("请输入家长密码: ")
        if guard.check_password(pw):
            return True
        warn("密码错误")
    err("密码校验失败")
    return False


def prompt_new_password(guard: Guard) -> bool:
    """引导设置家长主密码;成功写入返回 True。

    GUI 无 tty,通过环境变量 GROW_GUARD_NEWPW 传入新密码(已在图形界面二次确认);
    终端下则回退到 getpass 两次确认。
    """
    env_pw = os.environ.get("GROW_GUARD_NEWPW")
    if env_pw is not None:
        if len(env_pw) < 4:
            err("密码太短(至少 4 位)")
            return False
        guard.set_password(env_pw)
        ok("主密码已设置")
        return True
    print("设置家长主密码(用于解锁与修改限制)")
    pw1 = getpass.getpass("设置主密码: ")
    if len(pw1) < 4:
        err("密码太短(至少 4 位)")
        return False
    pw2 = getpass.getpass("再次输入: ")
    if pw1 != pw2:
        err("两次输入不一致")
        return False
    guard.set_password(pw1)
    ok("主密码已设置")
    return True


# ---------------------------------------------------------------------------
# 命令实现
# ---------------------------------------------------------------------------
def cmd_passwd(args):
    """修改家长主密码。默认需旧密码校验;--reset 由 root 授权重置(忘记旧密码时用)。

    --reset 的安全依据:本工具本就 root 运行,能 sudo 的即受信家长;标准(非管理员)
    子账户拿不到 sudo,无法用此路径重置,故不削弱对孩子的防护。
    """
    if not require_root_hint():
        return 1
    guard = Guard()
    reset = getattr(args, "reset", False)
    if guard.is_initialized() and not reset:
        if not require_password(guard):
            return 1
    elif reset and guard.is_initialized():
        warn("正在以 root 权限重置家长主密码(跳过旧密码校验)")
    # 新密码:GUI 无 tty 时经 GROW_GUARD_NEWPW 传入(已二次确认);终端下 getpass 两次确认。
    env_pw = os.environ.get("GROW_GUARD_NEWPW")
    if env_pw is not None:
        if len(env_pw) < 4:
            err("密码太短(至少 4 位)")
            return 1
        guard.set_password(env_pw)
        ok("主密码已重置" if reset else "主密码已更新")
        return 0
    pw1 = getpass.getpass("设置新主密码: ")
    if len(pw1) < 4:
        err("密码太短(至少 4 位)")
        return 1
    pw2 = getpass.getpass("再次输入: ")
    if pw1 != pw2:
        err("两次输入不一致")
        return 1
    guard.set_password(pw1)
    ok("主密码已重置" if reset else "主密码已更新")
    return 0


def cmd_limit(args):
    """设置 App 每日时长上限。加限制,免家长密码。"""
    if not require_root_hint():
        return 1
    guard = Guard()
    bundle_id = _resolve_bundle(args.app)
    if not bundle_id:
        return 1
    guard.set_app_limit(bundle_id, args.minutes)
    ok(f"已设置 {bundle_id} 每日上限 {args.minutes} 分钟")
    return 0


def cmd_lock_app(args):
    """直接禁用 / 解禁某 App。禁用是加限制免密码;解禁(--unblock)是放松限制,需家长密码。"""
    if not require_root_hint():
        return 1
    guard = Guard()
    blocked = not args.unblock
    if not blocked and not require_password(guard):
        return 1
    bundle_id = _resolve_bundle(args.app)
    if not bundle_id:
        return 1
    guard.set_app_blocked(bundle_id, blocked)
    ok(f"{bundle_id} 已{'禁用' if blocked else '解禁'}")
    return 0


def cmd_unlimit(args):
    """移除某 App 的所有限制。放松限制,需家长密码。"""
    if not require_root_hint():
        return 1
    guard = Guard()
    if not require_password(guard):
        return 1
    bundle_id = _resolve_bundle(args.app)
    if not bundle_id:
        return 1
    guard.remove_app(bundle_id)
    ok(f"已移除 {bundle_id} 的所有限制")
    return 0


def _resolve_bundle(app_arg: str):
    """把 App 名/路径解析为 bundle id;若本身就是 bundle id(含点)直接用。"""
    if "." in app_arg and "/" not in app_arg and not app_arg.endswith(".app"):
        return app_arg
    bid = core.bundle_id_for_app(app_arg)
    if not bid:
        err(f"无法解析 App: {app_arg}(试试完整路径或直接给 bundle id)")
        return None
    info(f"{app_arg} -> {bid}")
    return bid


def cmd_block_site(args):
    """加网站黑名单。加限制,免家长密码。"""
    if not require_root_hint():
        return 1
    guard = Guard()
    for d in args.domains:
        guard.add_site(d)
        ok(f"已加入网站黑名单: {d}")
    # 立即应用一次
    if not guard.in_grace():
        core.apply_hosts_block(guard.config["sites"])
        core.apply_pf_block(guard.config["sites"])
        ok("已应用屏蔽(hosts + PF)")
    return 0


def cmd_unblock_site(args):
    """移出网站黑名单。放松限制,需家长密码。"""
    if not require_root_hint():
        return 1
    guard = Guard()
    if not require_password(guard):
        return 1
    for d in args.domains:
        guard.remove_site(d)
        ok(f"已移出网站黑名单: {d}")
    core.apply_hosts_block(guard.config["sites"])
    core.apply_pf_block(guard.config["sites"])
    return 0


def cmd_schedule(args):
    """设置全局允许使用时间窗。启用/收紧时段免密码;关闭时段(--disable)是放松限制,需家长密码。"""
    if not require_root_hint():
        return 1
    guard = Guard()
    if args.disable:
        if not require_password(guard):
            return 1
        guard.config["schedule"]["enabled"] = False
        guard.save_config()
        ok("已关闭时间窗限制")
        return 0
    if args.start and args.end:
        guard.config["schedule"]["enabled"] = True
        guard.config["schedule"]["allow_start"] = args.start
        guard.config["schedule"]["allow_end"] = args.end
        guard.save_config()
        ok(f"已设置允许使用时段: {args.start} ~ {args.end}(其余时间锁定)")
        return 0
    err("请提供 --start 和 --end,或用 --disable 关闭")
    return 1


def cmd_unlock(args):
    """临时解锁:给一段宽限时间,期间所有限制暂停。"""
    if not require_root_hint():
        return 1
    guard = Guard()
    if not require_password(guard):
        return 1
    guard.grant_grace(args.minutes)
    ok(f"已临时解锁 {args.minutes} 分钟(期间 App 与网站限制暂停)")
    core.apply_hosts_block([])
    core.apply_pf_block([])
    return 0


def cmd_relock(args):
    """立即结束临时解锁,恢复所有限制。加限制方向,免家长密码。"""
    if not require_root_hint():
        return 1
    guard = Guard()
    guard.revoke_grace()
    core.apply_hosts_block(guard.config["sites"])
    core.apply_pf_block(guard.config["sites"])
    ok("已恢复所有限制")
    return 0


def _status_dict(guard) -> dict:
    """把当前状态序列化为 dict,供 --json 输出(GUI/前端消费)。"""
    sys_usage = core.system_usage_today()
    apps = []
    for bid, rule in guard.config.get("apps", {}).items():
        used = guard.state["usage_min"].get(bid, 0)
        if sys_usage is not None and bid in sys_usage:
            used = sys_usage[bid]
        apps.append({
            "bundle_id": bid,
            "daily_limit_min": rule.get("daily_limit_min"),
            "blocked": bool(rule.get("blocked")),
            "used_min": round(used, 1),
            "locked": guard.is_app_locked(bid),
        })
    sch = guard.config.get("schedule", {})
    grace_left = 0
    if guard.in_grace():
        import time
        grace_left = max(0, int((guard.config["grace"]["until_ts"] - time.time()) / 60))
    return {
        "initialized": guard.is_initialized(),
        "daemon_running": _daemon_running(),
        "usage_source": "knowledgeC" if sys_usage is not None else "poll",
        "grace_active": guard.in_grace(),
        "grace_left_min": grace_left,
        "schedule": {
            "enabled": bool(sch.get("enabled")),
            "allow_start": sch.get("allow_start"),
            "allow_end": sch.get("allow_end"),
            "in_window": guard.in_allowed_window(),
        },
        "apps": apps,
        "sites": list(guard.config.get("sites", [])),
    }


def cmd_status(args):
    """只读:展示当前配置与今日用量。无需密码。"""
    try:
        guard = Guard()
    except GuardError as e:
        if getattr(args, "json", False):
            import json as _json
            print(_json.dumps({"error": str(e)}))
        else:
            err(str(e))
        return 1
    if getattr(args, "json", False):
        import json as _json
        print(_json.dumps(_status_dict(guard), ensure_ascii=False))
        return 0
    print(f"\n{Colors.PURPLE}=== grow-guard 状态 ==={Colors.NC}")
    print(f"初始化: {'是' if guard.is_initialized() else '否'}")
    print(f"守护进程: {_daemon_status()}")
    sys_usage = core.system_usage_today()
    if sys_usage is None:
        print(f"用量来源: 轮询估算(未授予完全磁盘访问,精度 ±30s)")
    else:
        print(f"{Colors.GREEN}用量来源: 系统 knowledgeC(精确){Colors.NC}")
    grace = guard.in_grace()
    if grace:
        import time
        left = int((guard.config['grace']['until_ts'] - time.time()) / 60)
        print(f"{Colors.YELLOW}临时解锁中,剩余 ~{left} 分钟{Colors.NC}")

    sch = guard.config.get("schedule", {})
    if sch.get("enabled"):
        state = "允许" if guard.in_allowed_window() else "锁定"
        print(f"时间窗: {sch['allow_start']} ~ {sch['allow_end']}  当前: {state}")
    else:
        print("时间窗: 未启用")

    print(f"\n{Colors.CYAN}应用限制:{Colors.NC}")
    apps = guard.config.get("apps", {})
    if not apps:
        print("  (无)")
    for bid, rule in apps.items():
        used = guard.state["usage_min"].get(bid, 0)
        if sys_usage is not None and bid in sys_usage:
            used = sys_usage[bid]
        limit = rule.get("daily_limit_min")
        blocked = rule.get("blocked")
        locked = guard.is_app_locked(bid)
        mark = f"{Colors.RED}[锁定]{Colors.NC}" if locked else f"{Colors.GREEN}[可用]{Colors.NC}"
        if blocked:
            desc = "已禁用"
        elif limit is not None:
            desc = f"今日 {used:.0f}/{limit} 分钟"
        else:
            desc = "仅追踪"
        print(f"  {mark} {bid}: {desc}")

    print(f"\n{Colors.CYAN}网站黑名单:{Colors.NC}")
    sites = guard.config.get("sites", [])
    if not sites:
        print("  (无)")
    for d in sites:
        print(f"  - {d}")
    print()
    return 0


def cmd_list_apps(args):
    """列出已安装 App(供 GUI 勾选)。--json 输出机器可读列表。
    有今日用量的排前面(用得多的家长更关心)。"""
    apps = core.list_installed_apps()
    sys_usage = core.system_usage_today()
    if sys_usage:
        known = {a["bundle_id"] for a in apps}
        for a in apps:
            a["used_min"] = sys_usage.get(a["bundle_id"], 0)
        # 有用量但不在扫描目录里的 App(系统 App、非常规位置)也补进来,避免漏显示用量
        for bid, used in sys_usage.items():
            if bid in known or not used:
                continue
            extra = core.app_info_for_bundle(bid)
            if extra is None:
                extra = {"name": bid, "bundle_id": bid, "path": ""}
            extra["used_min"] = used
            apps.append(extra)
        apps.sort(key=lambda a: (-a.get("used_min", 0), a["name"].lower()))
    if getattr(args, "json", False):
        import json as _json
        print(_json.dumps(apps, ensure_ascii=False))
        return 0
    for a in apps:
        u = a.get("used_min")
        used = f"  今日 {u:.0f} 分钟" if u else ""
        print(f'{a["name"]}  ({a["bundle_id"]}){used}')
    return 0


def cmd_app_icon(args):
    """输出单个 App 的图标 data URI(供 GUI 懒加载)。找不到则输出空。"""
    uri = core.app_icon_data_uri(args.bundle_id)
    if uri:
        print(uri)
    return 0


def cmd_grant_fda(args):
    """引导授予完全磁盘访问,让 status 能读系统 knowledgeC 精确用量。"""
    if core.has_full_disk_access():
        ok("已授予完全磁盘访问,可显示系统精确用量")
        return 0
    info("为显示精确的系统 App 用量,需授予「完全磁盘访问」(仅用于只读展示,不影响拦截)")
    info("即将打开系统设置面板,请:")
    print("  1. 在列表中找到并勾选「青锁盾」(桌面 App;命令行运行时是 python3 / 终端)")
    print("  2. 若列表中没有,点 + 手动添加")
    print("  3. 授权后回到青锁盾即可看到精确用量")
    core.open_fda_settings()
    warn("提示:FDA 无法脚本自动授予,这是 Apple 的限制;未授权时自动回退到轮询估算")
    return 0


def cmd_screen_time(args):
    """打开系统「屏幕使用时间」设置面板。"""
    core.open_screen_time()
    ok("已打开系统「屏幕使用时间」")
    return 0


DAEMON_LABEL = "com.jtstudio.grow-guard"


def _daemon_running() -> bool:
    """判断守护进程是否在跑。

    关键:守护是 **system 域** LaunchDaemon(launchctl bootstrap system)。
    非 root 用户跑 `launchctl list` 只能看到自己 GUI/用户域的任务,看不到
    system 域的守护 —— 这正是 GUI(以普通用户身份调 status)一直显示
    "未启用" 的根因。因此这里改用两条都能在**非特权**下反映真实状态的判据:

    1. pgrep 匹配真实存活的守护进程(最可靠,直接看进程是否在跑);
    2. root 时再用 `launchctl print system/<label>` 兜底(确认已注册)。
    """
    # 1) 进程真在跑?—— 非特权可用,直接反映现实
    try:
        r = subprocess.run(
            ["/usr/bin/pgrep", "-f", "cli.py daemon"],
            capture_output=True, text=True, timeout=5,
        )
        if r.returncode == 0 and r.stdout.strip():
            return True
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass

    # 2) root 时查 system 域是否已注册(进程可能刚好在重启间隙)
    if is_root():
        try:
            r = subprocess.run(
                ["/bin/launchctl", "print", f"system/{DAEMON_LABEL}"],
                capture_output=True, text=True, timeout=5,
            )
            if r.returncode == 0:
                return True
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass

    return False


def _daemon_status() -> str:
    if _daemon_running():
        return f"{Colors.GREEN}运行中{Colors.NC}"
    return f"{Colors.YELLOW}未运行{Colors.NC}"


def cmd_install(args):
    if not require_root_hint():
        return 1
    guard = Guard()
    # 首次安装:自动引导设置家长密码(取代旧的独立 init 命令)
    if not guard.is_initialized():
        info("首次安装 —— 需要先设置家长主密码")
        if not prompt_new_password(guard):
            return 1
    if not INSTALL_SCRIPT.exists():
        err(f"安装脚本缺失: {INSTALL_SCRIPT}")
        return 1
    rc = subprocess.call(["/bin/bash", str(INSTALL_SCRIPT), "install"])
    return rc


def cmd_uninstall(args):
    """防卸载:必须密码校验通过才允许卸载;成功后清除密码配置。"""
    if not require_root_hint():
        return 1
    guard = Guard()
    if guard.is_initialized():
        warn("卸载访问锁需要家长密码验证")
        if not require_password(guard):
            err("密码校验失败,拒绝卸载")
            return 1
    rc = subprocess.call(["/bin/bash", str(INSTALL_SCRIPT), "uninstall"])
    if rc == 0:
        # 卸载成功后彻底清掉配置目录(含密码/密钥/用量),下次安装重新设密码
        try:
            shutil.rmtree(core.GUARD_HOME, ignore_errors=True)
            ok("已卸载守护进程、清除所有屏蔽规则及家长密码")
        except OSError as e:
            warn(f"守护进程已卸载,但清除配置目录失败: {e}")
    return rc


def cmd_daemon(args):
    """前台运行守护逻辑(供 LaunchDaemon 调用,也可手动调试)。"""
    import daemon
    return daemon.main()


# ---------------------------------------------------------------------------
# 参数解析
# ---------------------------------------------------------------------------
def build_parser():
    p = argparse.ArgumentParser(
        prog="grow-guard",
        description="青锁盾 青少年访问锁:应用限制 + 网站过滤 + 时间管理 + 家长密码 + 防卸载",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
示例:
  sudo grow-guard install                       # 安装守护进程(首次会引导设密码)
  sudo grow-guard limit Safari 60               # Safari 每日限用 60 分钟
  sudo grow-guard limit com.tencent.xin 30      # 直接用 bundle id
  sudo grow-guard lock-app "Game Center"        # 直接禁用某 App
  sudo grow-guard block-site youtube.com bilibili.com
  sudo grow-guard schedule --start 07:00 --end 21:30   # 只允许这段时间使用
  sudo grow-guard unlock 30                      # 临时解锁 30 分钟
  grow-guard status                              # 查看状态(无需密码)
  sudo grow-guard uninstall                      # 卸载(需家长密码)
""",
    )
    sub = p.add_subparsers(dest="command")

    sp = sub.add_parser("passwd", help="修改家长主密码(--reset 由 root 重置,忘记旧密码时用)")
    sp.add_argument("--reset", action="store_true",
                    help="root 授权重置:跳过旧密码校验,直接设新密码")
    sp.set_defaults(func=cmd_passwd)

    sp = sub.add_parser("limit", help="设置 App 每日时长上限(分钟)")
    sp.add_argument("app", help="App 名称 / 路径 / bundle id")
    sp.add_argument("minutes", type=int, help="每日上限分钟数")
    sp.set_defaults(func=cmd_limit)

    sp = sub.add_parser("lock-app", help="直接禁用/解禁某 App")
    sp.add_argument("app", help="App 名称 / 路径 / bundle id")
    sp.add_argument("--unblock", action="store_true", help="解禁(默认为禁用)")
    sp.set_defaults(func=cmd_lock_app)

    sp = sub.add_parser("unlimit", help="移除某 App 的所有限制")
    sp.add_argument("app", help="App 名称 / 路径 / bundle id")
    sp.set_defaults(func=cmd_unlimit)

    sp = sub.add_parser("block-site", help="添加网站黑名单")
    sp.add_argument("domains", nargs="+", help="一个或多个域名")
    sp.set_defaults(func=cmd_block_site)

    sp = sub.add_parser("unblock-site", help="移除网站黑名单")
    sp.add_argument("domains", nargs="+", help="一个或多个域名")
    sp.set_defaults(func=cmd_unblock_site)

    sp = sub.add_parser("schedule", help="设置全局允许使用时间窗")
    sp.add_argument("--start", help="允许开始时间 HH:MM")
    sp.add_argument("--end", help="允许结束时间 HH:MM")
    sp.add_argument("--disable", action="store_true", help="关闭时间窗限制")
    sp.set_defaults(func=cmd_schedule)

    sp = sub.add_parser("unlock", help="临时解锁(暂停所有限制)")
    sp.add_argument("minutes", type=int, nargs="?", default=15, help="解锁分钟数(默认 15)")
    sp.set_defaults(func=cmd_unlock)

    sp = sub.add_parser("relock", help="立即结束临时解锁")
    sp.set_defaults(func=cmd_relock)

    sp = sub.add_parser("status", help="查看当前状态(无需密码)")
    sp.add_argument("--json", action="store_true", help="以 JSON 输出(供 GUI/脚本消费)")
    sp.set_defaults(func=cmd_status)

    sp = sub.add_parser("list-apps", help="列出已安装 App(供 GUI 勾选)")
    sp.add_argument("--json", action="store_true", help="以 JSON 输出")
    sp.set_defaults(func=cmd_list_apps)

    sp = sub.add_parser("app-icon", help="[内部] 输出单个 App 图标 data URI")
    sp.add_argument("bundle_id", help="App bundle id")
    sp.set_defaults(func=cmd_app_icon)

    sp = sub.add_parser("grant-fda", help="引导授予完全磁盘访问(让 status 显示系统精确用量)")
    sp.set_defaults(func=cmd_grant_fda)

    sp = sub.add_parser("screen-time", help="打开系统「屏幕使用时间」设置面板")
    sp.set_defaults(func=cmd_screen_time)

    sp = sub.add_parser("install", help="安装守护进程(开机自启+防卸载)")
    sp.set_defaults(func=cmd_install)

    sp = sub.add_parser("uninstall", help="卸载守护进程(需家长密码)")
    sp.set_defaults(func=cmd_uninstall)

    sp = sub.add_parser("daemon", help="[内部] 前台运行守护逻辑")
    sp.set_defaults(func=cmd_daemon)

    return p


def main():
    parser = build_parser()
    args = parser.parse_args()
    if not getattr(args, "command", None):
        parser.print_help()
        return 0
    try:
        return args.func(args)
    except GuardError as e:
        err(str(e))
        return 1
    except KeyboardInterrupt:
        print()
        return 130


if __name__ == "__main__":
    sys.exit(main())
