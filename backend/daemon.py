#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
grow-guard 守护进程

由 root LaunchDaemon 拉起(KeepAlive=true,被 kill 会自动重启)。

每个轮询周期(默认 30s):
1. 探测当前运行的受限 App,按周期累加使用分钟数到 state。
2. 判定每个受限 App 是否到达上限 / 被手动禁用 / 处于禁用时间窗 —— 是则隐藏它并弹通知。
3. 重新应用网站屏蔽(hosts + PF),防止有人手动清掉。
4. 跨天时 state 自动重置(由 Guard._load_state 处理)。

不做的事:不强杀进程(保数据),只隐藏到后台并提示。
"""

import os
import sys
import time
import signal
import stat
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import core
from core import Guard, Colors

POLL_INTERVAL = 8           # 秒:完整巡检(累加用量、重应用网站屏蔽)
FRONT_CHECK_EVERY = 0.5     # 秒:前台快检(切到被锁 App 焦点即遮挡,近实时)
SITE_REAPPLY_EVERY = 10     # 每 N 个完整周期重新应用一次网站屏蔽(约 80 秒)
SELF_BUNDLE_ID = "com.jtstudio.growguard"   # 青锁盾 app 自身,遮罩窗前台时不清信号

_running = True


def _log(msg: str) -> None:
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}\n"
    try:
        with open(core.LOG_PATH, "a") as f:
            f.write(line)
    except OSError:
        pass
    print(line, end="", flush=True)


def _handle_signal(signum, frame):
    global _running
    _running = False
    _log(f"收到信号 {signum},准备退出")


def _lock_reason(guard: Guard, bundle_id: str, rule: dict, window_ok: bool) -> str:
    if rule.get("blocked"):
        return "该应用已被禁用"
    if not window_ok:
        return "当前不在允许使用时段"
    limit = rule.get("daily_limit_min")
    if limit is not None:
        used = guard.state["usage_min"].get(bundle_id, 0)
        return f"今日使用时长已用完(上限 {limit} 分钟, 已用 {used:.1f} 分钟)"
    return "已锁定"


def enforce_once(guard: Guard) -> None:
    """单次巡检:执行 App 锁定。用量不在此累加,由前台快检按驻留时长同步。"""
    running = core.running_bundle_ids()

    window_ok = guard.in_allowed_window()

    for bundle_id in list(guard.config["apps"].keys()):
        is_running = bundle_id in running

        should_lock = guard.is_app_locked(bundle_id) or (not window_ok and not guard.in_grace())

        if should_lock and is_running:
            rule = guard.config["apps"].get(bundle_id, {})
            reason = _lock_reason(guard, bundle_id, rule, window_ok)
            app_name = core.app_name_for_bundle(bundle_id)
            acted = core.enforce_app_lock(bundle_id, app_name, reason)
            if acted:
                _log(f"遮挡 App {bundle_id}({reason})")

    guard.save_state()


def reapply_sites(guard: Guard) -> None:
    domains = guard.config.get("sites", [])
    if guard.in_grace():
        # 宽限期内解除网站屏蔽
        try:
            core.apply_hosts_block([])
            core.apply_pf_block([])
        except Exception as e:  # noqa: BLE001
            _log(f"宽限期清除网站屏蔽失败: {e}")
        return
    try:
        core.apply_hosts_block(domains)
        core.apply_pf_block(domains)
        if domains:
            _log(f"已应用网站屏蔽: {len(domains)} 个域名")
    except Exception as e:  # noqa: BLE001
        _log(f"应用网站屏蔽失败: {e}")


def _self_integrity_ok() -> bool:
    """守护代码必须 root 属主且非 group/world 可写,否则拒绝运行 —— 防止有人改
    源码让 root 执行任意代码。安装脚本把代码放在 root 独占的 libexec 目录。"""
    src_dir = Path(__file__).resolve().parent
    for f in src_dir.glob("*.py"):
        try:
            st = f.stat()
        except OSError:
            return False
        if st.st_uid != 0:
            _log(f"拒绝运行:{f} 属主非 root(uid={st.st_uid})")
            return False
        if st.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
            _log(f"拒绝运行:{f} 存在组/其他人写权限")
            return False
    return True


_last_front = None   # 上次前台 bundle id;仅在焦点变化时打日志,避免每秒刷屏
_dwell_bundle = None    # 当前正在计时的前台 App
_dwell_since = 0.0      # 该 App 成为前台的起始时刻(wall clock)


def _sync_dwell(guard: Guard, front, now: float) -> None:
    """把前台 App 的真实驻留时长同步进 state(set,非累加)。
    knowledgeC 守护(root)读不到,故自行以 wall-clock 计前台停留,口径与系统前台时长一致:
    后台常驻进程不计,只有正在前台被用的那个 App 在走时。
    切换前台时:先把上一个前台 App 的剩余驻留结算掉,再把计时基准切到新前台,
    保证 fast_front_check 随后对新前台的判定一定基于已更新的用量。"""
    global _dwell_bundle, _dwell_since
    prev = _dwell_bundle
    if prev is not None and prev == front:
        _credit_dwell(guard, prev, now)
        _dwell_since = now
        return
    if prev is not None:
        _credit_dwell(guard, prev, now)
    _dwell_bundle = front
    _dwell_since = now


def _credit_dwell(guard: Guard, bundle_id, now: float) -> None:
    if bundle_id is None or bundle_id not in guard.config["apps"]:
        return
    if guard.in_grace() or not guard.in_allowed_window():
        return
    elapsed_min = (now - _dwell_since) / 60.0
    if elapsed_min <= 0:
        return
    base = guard.state["usage_min"].get(bundle_id, 0)
    guard.set_usage(bundle_id, base + elapsed_min)
    guard.save_state()


def fast_front_check(guard: Guard) -> None:
    """前台快检:只看当前最前台 App。若它被锁 -> 写遮挡信号(app 弹全屏遮罩)+ 隐藏兜底。
    每 ~1 秒跑一次,做到"切到禁用 App 焦点就锁上",开销只有一次 frontmost 查询。"""
    global _last_front
    if guard.in_grace():
        core.clear_block_signal()
        return
    front = core.frontmost_bundle_id()
    _sync_dwell(guard, front, time.time())
    if not front:
        return
    changed = front != _last_front
    _last_front = front
    if front == SELF_BUNDLE_ID:
        return
    window_ok = guard.in_allowed_window()
    rule = guard.config["apps"].get(front, {})
    is_blocked = bool(rule.get("blocked"))
    locked = guard.is_app_locked(front) or (not window_ok)
    if changed:
        _log(
            f"[判定] 前台={front} 受限规则存在={bool(rule)} "
            f"已禁用={is_blocked} 时间窗OK={window_ok} => 需遮挡={locked}"
        )
    if not locked:
        core.clear_block_signal()
        return
    if is_blocked:
        reason = "该应用已被禁用"
    elif not window_ok:
        reason = "当前不在允许使用时段"
    else:
        reason = _lock_reason(guard, front, rule, window_ok)
    app_name = core.app_name_for_bundle(front)
    core.write_block_signal(front, app_name, reason)
    acted = core.enforce_app_lock(front, app_name, reason)
    if changed:
        _log(f"[遮挡] {app_name} -> 已写遮罩信号 + 隐藏兜底({reason}) acted={acted}")


def main() -> int:
    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    _log("grow-guard 守护进程启动")

    if os.geteuid() == 0 and not _self_integrity_ok():
        _log("自校验失败:守护代码不可信,退出(请重新 sudo grow-guard install)")
        return 1

    if os.geteuid() == 0:
        try:
            core.migrate_password_out_of_config()
        except Exception as e:  # noqa: BLE001
            _log(f"密码迁移 auth.json 失败(忽略): {e}")

    cycle = 0
    last_grace = None
    last_good = None   # 最近一次成功读取的策略;篡改时兜底继续执行

    while _running:
        try:
            guard = Guard()  # 每周期重读,拿到 CLI 的最新改动
            last_good = guard
        except core.GuardError as e:
            # fail-closed:配置被篡改时不放开限制,用上一份可信策略继续执行
            if last_good is not None:
                _log(f"配置读取失败(篡改?): {e} —— 沿用上一份可信策略继续锁定")
                try:
                    enforce_once(last_good)
                    reapply_sites(last_good)
                except Exception as ee:  # noqa: BLE001
                    _log(f"兜底执行异常: {ee}")
            else:
                _log(f"配置读取失败(篡改?): {e} —— 无可信策略,5s 后重试")
            time.sleep(5)
            continue

        if not guard.is_initialized():
            _log("尚未初始化(无主密码),空转等待")
            time.sleep(POLL_INTERVAL)
            continue

        try:
            enforce_once(guard)
        except Exception as e:  # noqa: BLE001
            _log(f"App 巡检异常: {e}")

        # 宽限状态切换时立即重应用网站规则
        grace_now = guard.in_grace()
        if cycle % SITE_REAPPLY_EVERY == 0 or grace_now != last_grace:
            reapply_sites(guard)
        last_grace = grace_now

        cycle += 1
        # 在两次完整巡检之间,以 ~1.2s 的节奏跑前台快检:切到被锁 App 焦点即遮挡(近实时)。
        # 快检只查一次 frontmost,开销远小于完整巡检,故可高频运行。
        deadline = time.time() + POLL_INTERVAL
        while _running and time.time() < deadline:
            time.sleep(FRONT_CHECK_EVERY)
            try:
                fast_front_check(guard)
            except Exception as e:  # noqa: BLE001
                _log(f"前台快检异常: {e}")

    # 退出前清掉遮挡信号,避免守护停了遮罩还卡在屏幕上
    try:
        core.clear_block_signal()
    except Exception:  # noqa: BLE001
        pass
    _log("grow-guard 守护进程退出")
    return 0


if __name__ == "__main__":
    sys.exit(main())
