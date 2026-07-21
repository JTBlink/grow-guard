#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
grow-guard 核心库

职责:
- 配置读写 + HMAC 签名校验(防篡改)
- 主密码管理(PBKDF2 哈希)
- App 使用时长追踪(累计 / 每日重置)
- 网站屏蔽(hosts + PF 防火墙 anchor 双层)
- 临时解锁(宽限时间窗)

设计约束:
- 防技术型青少年:配置签名化,守护进程 root 运行,kill 后自拉起(由 LaunchDaemon 保证)
- 到点动作是"禁止启动 App",不强杀正在运行的进程
"""

import os
import sys
import json
import time
import hmac
import hashlib
import secrets
import subprocess
from datetime import datetime, date
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # Python < 3.11
    try:
        import tomli as tomllib
    except ModuleNotFoundError:
        tomllib = None


class Colors:
    RED = '\033[0;31m'
    GREEN = '\033[0;32m'
    YELLOW = '\033[1;33m'
    BLUE = '\033[0;34m'
    PURPLE = '\033[0;35m'
    CYAN = '\033[0;36m'
    NC = '\033[0m'


# ---------------------------------------------------------------------------
# 路径约定
# ---------------------------------------------------------------------------
# 状态与配置放在系统级目录,需 root 写入,普通用户只读 —— 防技术型绕过的关键。
GUARD_HOME = Path(os.environ.get("GROW_GUARD_HOME", "/Library/Application Support/GrowGuard/data"))
CONFIG_PATH = GUARD_HOME / "config.json"          # 主配置(签名保护,0644 只读展示)
STATE_PATH = GUARD_HOME / "state.json"            # 用量状态(签名保护)
SECRET_PATH = GUARD_HOME / "guard.key"            # HMAC 密钥(仅 root 可读, 0600)
AUTH_PATH = GUARD_HOME / "auth.json"              # 家长密码哈希(仅 root 可读, 0600)
LOG_PATH = GUARD_HOME / "guard.log"
# 遮挡信号:root 守护进程写、用户级青锁盾 app 读,驱动全屏遮罩(root 画不出窗,只能靠 app)。
# 0644 让非 root 的 app 能读;内容非敏感(仅当前被挡应用名/原因)。
BLOCK_SIGNAL_PATH = GUARD_HOME / "block_signal.json"

HOSTS_PATH = Path("/etc/hosts")
HOSTS_MARK_BEGIN = "# >>> grow-guard managed block >>>"
HOSTS_MARK_END = "# <<< grow-guard managed block <<<"

PF_ANCHOR_NAME = "grow-guard"
PF_ANCHOR_FILE = Path("/etc/pf.anchors/grow-guard")


class GuardError(Exception):
    """核心库统一异常。"""


# ---------------------------------------------------------------------------
# 签名 / 密钥
# ---------------------------------------------------------------------------
def _load_secret() -> bytes:
    """加载 HMAC 密钥;不存在则生成(仅 root 环境应能写)。
    密钥存在但读不到(非 root 只读调用)时抛 GuardError,由上层降级处理,而非崩溃。"""
    if SECRET_PATH.exists():
        try:
            return SECRET_PATH.read_bytes()
        except PermissionError as e:
            raise GuardError(f"无权读取签名密钥(需 root): {SECRET_PATH}") from e
    GUARD_HOME.mkdir(parents=True, exist_ok=True)
    key = secrets.token_bytes(32)
    SECRET_PATH.write_bytes(key)
    try:
        os.chmod(SECRET_PATH, 0o600)
    except PermissionError:
        pass
    return key


def secret_readable() -> bool:
    """当前进程能否读到 HMAC 密钥。非 root 只读调用者据此走"不校验签名"的降级读。"""
    if not SECRET_PATH.exists():
        return True
    return os.access(SECRET_PATH, os.R_OK)


def _sign(payload: dict) -> str:
    """对 payload(不含 _sig 字段)计算 HMAC-SHA256。"""
    key = _load_secret()
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hmac.new(key, canonical, hashlib.sha256).hexdigest()


def _verify(data: dict) -> bool:
    """校验 data 中的 _sig 是否匹配。缺失 _sig 或无法读密钥均视为不可信(False)。"""
    sig = data.get("_sig")
    if not sig:
        return False
    payload = {k: v for k, v in data.items() if k != "_sig"}
    try:
        expected = _sign(payload)
    except GuardError:
        return False
    return hmac.compare_digest(sig, expected)


def _write_signed(path: Path, payload: dict) -> None:
    payload = {k: v for k, v in payload.items() if k != "_sig"}
    payload["_sig"] = _sign(payload)
    GUARD_HOME.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False))
    os.replace(tmp, path)
    try:
        # config/state 无密码哈希(已迁 auth.json),0644 让非 root 的 status/gui 只读展示
        os.chmod(path, 0o644)
    except PermissionError:
        pass


def _read_signed(path: Path, *, strict: bool = True) -> dict:
    """读取签名文件。
    - 能读密钥(root/守护)时:strict=True 签名不符即抛错(视为篡改)。
    - 读不到密钥(非 root 只读,如 status/gui)时:无法校验签名,降级为只读加载,
      不崩溃(只读展示不构成安全边界;真正的强制由 root 守护进程负责)。"""
    if not path.exists():
        return {}
    try:
        raw = path.read_text()
    except PermissionError as e:
        raise GuardError(f"无权读取配置(需 root): {path}") from e
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        raise GuardError(f"配置文件损坏: {path}: {e}")
    if not secret_readable():
        return {k: v for k, v in data.items() if k != "_sig"}
    if not _verify(data):
        if strict:
            raise GuardError(f"配置签名校验失败(可能被篡改): {path}")
        return {}
    return {k: v for k, v in data.items() if k != "_sig"}


# ---------------------------------------------------------------------------
# 主密码(PBKDF2)
# ---------------------------------------------------------------------------
def hash_password(password: str, salt: "bytes | None" = None) -> dict:
    salt = salt or secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 200_000)
    return {"salt": salt.hex(), "hash": dk.hex(), "iter": 200_000}


def verify_password(password: str, record: dict) -> bool:
    if not record:
        return False
    salt = bytes.fromhex(record["salt"])
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, record.get("iter", 200_000))
    return hmac.compare_digest(dk.hex(), record["hash"])


def _read_auth() -> dict:
    if not AUTH_PATH.exists():
        return {}
    try:
        return json.loads(AUTH_PATH.read_text())
    except (PermissionError, json.JSONDecodeError):
        return {}


def _write_auth(record: dict) -> None:
    GUARD_HOME.mkdir(parents=True, exist_ok=True)
    tmp = AUTH_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(record))
    os.replace(tmp, AUTH_PATH)
    try:
        os.chmod(AUTH_PATH, 0o600)
    except PermissionError:
        pass


def migrate_password_out_of_config() -> None:
    """把旧版 config.json 里的 password 哈希迁到 root-only auth.json,再从 config 删除。
    仅 root 有意义(要能写两个文件)。幂等。"""
    if AUTH_PATH.exists() or not CONFIG_PATH.exists():
        return
    try:
        data = json.loads(CONFIG_PATH.read_text())
    except (PermissionError, json.JSONDecodeError):
        return
    pw = data.get("password")
    if not pw:
        return
    _write_auth(pw)


# ---------------------------------------------------------------------------
# 配置模型
# ---------------------------------------------------------------------------
DEFAULT_CONFIG = {
    "version": 1,
    "apps": {},                   # {"com.apple.Safari": {"daily_limit_min": 60, "blocked": false}}
    "sites": [],                  # ["example.com", "www.example.com"]
    "grace": {                    # 临时解锁窗口
        "until_ts": 0,            # 解锁有效期截止时间戳;> now 时暂停所有锁定
    },
    "schedule": {                 # 全局允许使用时间窗(可选)
        "enabled": False,
        "allow_start": "07:00",
        "allow_end": "21:30",
    },
}


class Guard:
    """核心状态机。CLI 与 daemon 都通过它读写。"""

    def __init__(self):
        self.config = self._load_config()
        self.state = self._load_state()

    # ---- 配置 ----
    def _load_config(self) -> dict:
        cfg = _read_signed(CONFIG_PATH, strict=True) if CONFIG_PATH.exists() else {}
        cfg.pop("password", None)   # 密码已迁至 auth.json,忽略 config 里的遗留字段
        merged = json.loads(json.dumps(DEFAULT_CONFIG))
        merged.update(cfg or {})
        for k, v in DEFAULT_CONFIG.items():
            if isinstance(v, dict):
                merged.setdefault(k, {})
                base = dict(v)
                base.update(merged[k] or {})
                merged[k] = base
        return merged

    def save_config(self) -> None:
        _write_signed(CONFIG_PATH, self.config)

    # ---- 状态(每日用量)----
    def _load_state(self) -> dict:
        # 签名校验失败(被改过用量)时,丢弃并重置为今日空用量 —— 篡改不获益。
        try:
            st = _read_signed(STATE_PATH, strict=True) if STATE_PATH.exists() else {}
        except GuardError:
            st = {}
        today = date.today().isoformat()
        if st.get("date") != today:
            st = {"date": today, "usage_min": {}}  # {bundle_id: minutes}
        st.setdefault("usage_min", {})
        return st

    def save_state(self) -> None:
        _write_signed(STATE_PATH, self.state)

    # ---- 密码(存于 root-only auth.json,防非 root 子账户读哈希离线爆破)----
    def is_initialized(self) -> bool:
        return AUTH_PATH.exists()

    def set_password(self, password: str) -> None:
        _write_auth(hash_password(password))

    def check_password(self, password: str) -> bool:
        return verify_password(password, _read_auth())

    # ---- App 限制 ----
    def set_app_limit(self, bundle_id: str, daily_limit_min: int) -> None:
        self.config["apps"].setdefault(bundle_id, {})
        self.config["apps"][bundle_id]["daily_limit_min"] = int(daily_limit_min)
        self.config["apps"][bundle_id].setdefault("blocked", False)
        self.save_config()

    def set_app_blocked(self, bundle_id: str, blocked: bool) -> None:
        self.config["apps"].setdefault(bundle_id, {})
        self.config["apps"][bundle_id]["blocked"] = bool(blocked)
        self.save_config()

    def remove_app(self, bundle_id: str) -> None:
        self.config["apps"].pop(bundle_id, None)
        self.save_config()

    def add_usage(self, bundle_id: str, minutes: float) -> None:
        cur = self.state["usage_min"].get(bundle_id, 0)
        self.state["usage_min"][bundle_id] = round(cur + minutes, 2)

    def set_usage(self, bundle_id: str, minutes: float) -> None:
        self.state["usage_min"][bundle_id] = round(max(0.0, minutes), 4)

    def is_app_locked(self, bundle_id: str) -> bool:
        """判断某 App 当前是否应被禁止启动。"""
        if self.in_grace():
            return False
        rule = self.config["apps"].get(bundle_id)
        if not rule:
            return False
        if rule.get("blocked"):
            return True
        limit = rule.get("daily_limit_min")
        if limit is not None:
            used = self.state["usage_min"].get(bundle_id, 0)
            if used >= limit:
                return True
        return False

    def app_remaining_min(self, bundle_id: str):
        rule = self.config["apps"].get(bundle_id, {})
        limit = rule.get("daily_limit_min")
        if limit is None:
            return None
        used = self.state["usage_min"].get(bundle_id, 0)
        return max(0, limit - used)

    # ---- 临时解锁 ----
    def in_grace(self) -> bool:
        return time.time() < self.config.get("grace", {}).get("until_ts", 0)

    def grant_grace(self, minutes: int) -> None:
        self.config.setdefault("grace", {})
        self.config["grace"]["until_ts"] = time.time() + minutes * 60
        self.save_config()

    def revoke_grace(self) -> None:
        self.config.setdefault("grace", {})
        self.config["grace"]["until_ts"] = 0
        self.save_config()

    # ---- 时间窗 ----
    def in_allowed_window(self) -> bool:
        sch = self.config.get("schedule", {})
        if not sch.get("enabled"):
            return True
        now = datetime.now().strftime("%H:%M")
        start = sch.get("allow_start", "00:00")
        end = sch.get("allow_end", "23:59")
        if start <= end:
            return start <= now <= end
        # 跨午夜
        return now >= start or now <= end

    # ---- 网站 ----
    def add_site(self, domain: str) -> None:
        domain = domain.strip().lower()
        if domain and domain not in self.config["sites"]:
            self.config["sites"].append(domain)
            self.save_config()

    def remove_site(self, domain: str) -> None:
        domain = domain.strip().lower()
        if domain in self.config["sites"]:
            self.config["sites"].remove(domain)
            self.save_config()


# ---------------------------------------------------------------------------
# 系统 App 使用时长(knowledgeC.db,只读用于 status 展示)
# ---------------------------------------------------------------------------
# knowledgeC 记录了系统级 App 使用时长,但受 TCC 保护:进程需被授予「完全磁盘
# 访问」才能读。无法脚本静默授予,只能引导家长在系统设置里手动勾选。因此这里只
# 用于 status 展示更精确的历史用量;实时拦截仍靠 lsappinfo 轮询,不依赖它。
KNOWLEDGE_DB_CANDIDATES = [
    Path("/private/var/db/CoreDuet/Knowledge/knowledgeC.db"),
    Path.home() / "Library/Application Support/Knowledge/knowledgeC.db",
]
FDA_SETTINGS_URL = (
    "x-apple.systempreferences:com.apple.preference.security"
    "?Privacy_AllFiles"
)
SCREEN_TIME_URL = "x-apple.systempreferences:com.apple.Screen-Time-Settings.extension"


def knowledgec_db_path():
    for p in KNOWLEDGE_DB_CANDIDATES:
        # p.exists() 在 TCC 保护路径上会抛 PermissionError(连 stat 都不允许),
        # 视为不可访问跳过,而非崩溃 —— 未授予 FDA 时回退到轮询用量。
        try:
            if p.exists():
                return p
        except OSError:
            continue
    return None


def has_full_disk_access() -> bool:
    """能实际打开 knowledgeC.db 即视为已授予 FDA;打不开(权限拒绝)返回 False。"""
    import sqlite3
    db = knowledgec_db_path()
    if not db:
        return False
    try:
        con = sqlite3.connect(f"file:{db}?mode=ro&immutable=1", uri=True, timeout=2)
        con.execute("SELECT 1 FROM ZOBJECT LIMIT 1").fetchone()
        con.close()
        return True
    except Exception:  # noqa: BLE001
        return False


def system_usage_today() -> "dict | None":
    """
    读 knowledgeC 今日各 App 前台使用秒数 -> {bundle_id: minutes}。
    未授予 FDA / 读取失败返回 None,调用方据此回退到轮询用量。
    """
    import sqlite3
    db = knowledgec_db_path()
    if not db:
        return None
    # Cocoa/Mac epoch = 2001-01-01;knowledgeC 时间戳以此为基准。
    mac_epoch_offset = 978307200
    start_of_day = datetime.now().replace(
        hour=0, minute=0, second=0, microsecond=0
    ).timestamp() - mac_epoch_offset
    q = (
        "SELECT ZOBJECT.ZVALUESTRING, "
        "SUM(ZOBJECT.ZENDDATE - ZOBJECT.ZSTARTDATE) "
        "FROM ZOBJECT "
        "WHERE ZOBJECT.ZSTREAMNAME = '/app/usage' "
        "AND ZOBJECT.ZSTARTDATE >= ? "
        "GROUP BY ZOBJECT.ZVALUESTRING"
    )
    try:
        con = sqlite3.connect(f"file:{db}?mode=ro&immutable=1", uri=True, timeout=3)
        rows = con.execute(q, (start_of_day,)).fetchall()
        con.close()
    except Exception:  # noqa: BLE001
        return None
    usage = {}
    for bundle_id, seconds in rows:
        if bundle_id and seconds:
            usage[bundle_id] = round(seconds / 60.0, 1)
    return usage


def open_fda_settings() -> None:
    """打开系统设置的「完全磁盘访问」面板,引导家长手动授权。"""
    try:
        subprocess.run(["/usr/bin/open", FDA_SETTINGS_URL],
                       capture_output=True, timeout=5)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass


def open_screen_time() -> None:
    """打开系统设置的「屏幕使用时间」面板。"""
    try:
        subprocess.run(["/usr/bin/open", SCREEN_TIME_URL],
                       capture_output=True, timeout=5)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass


# ---------------------------------------------------------------------------
# 系统操作:App 运行探测 / 网站屏蔽
# ---------------------------------------------------------------------------
def running_bundle_ids() -> dict:
    """
    返回当前运行的 GUI App: {bundle_id: pid}
    使用 lsappinfo(macOS 内置),无需第三方依赖。
    """
    result = {}
    try:
        out = subprocess.run(
            ["/usr/bin/lsappinfo", "list"],
            capture_output=True, text=True, timeout=10
        ).stdout
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return result

    cur_pid = None
    for line in out.splitlines():
        line = line.strip()
        if "pid = " in line:
            try:
                cur_pid = int(line.split("pid = ")[1].split()[0])
            except (IndexError, ValueError):
                cur_pid = None
        if "bundleID=" in line:
            bid = line.split('bundleID="')[-1].split('"')[0]
            if bid and cur_pid:
                result[bid] = cur_pid
    return result


def bundle_id_for_app(app_name_or_path: str):
    """
    尝试把 App 名或路径解析为 bundle id。
    支持: 'Safari' / 'Safari.app' / '/Applications/Safari.app'
    """
    candidates = []
    p = Path(app_name_or_path)
    if p.exists() and p.suffix == ".app":
        candidates.append(p)
    else:
        name = app_name_or_path
        if not name.endswith(".app"):
            name += ".app"
        for base in ["/Applications", "/System/Applications",
                     str(Path.home() / "Applications")]:
            cand = Path(base) / name
            if cand.exists():
                candidates.append(cand)
    for app in candidates:
        plist = app / "Contents" / "Info.plist"
        if plist.exists():
            try:
                out = subprocess.run(
                    ["/usr/libexec/PlistBuddy", "-c",
                     "Print :CFBundleIdentifier", str(plist)],
                    capture_output=True, text=True, timeout=5
                )
                bid = out.stdout.strip()
                if bid:
                    return bid
            except (FileNotFoundError, subprocess.TimeoutExpired):
                pass
    return None


def _iter_app_bundles(base: Path, max_depth: int = 2):
    """在 base 下查找 .app(含嵌套,如 Adobe 系列放在子目录里)。
    到 .app 即停,不再往里递归;限制深度避免遍历超大目录树。"""
    if not base.is_dir():
        return
    stack = [(base, 0)]
    while stack:
        d, depth = stack.pop()
        try:
            entries = list(d.iterdir())
        except OSError:
            continue
        for e in entries:
            if e.suffix == ".app" and e.is_dir():
                yield e
            elif e.is_dir() and not e.name.startswith(".") and depth < max_depth:
                stack.append((e, depth + 1))


APP_SCAN_DIRS = [
    "/Applications",
    "/System/Applications",
    "/System/Applications/Utilities",
    "/System/Library/CoreServices",
    "/System/Library/CoreServices/Applications",
]


def list_installed_apps() -> list:
    """扫描标准目录列出已安装 App:[{"name","bundle_id","path"}],按名称排序。
    供 GUI 勾选限制用。用 plistlib(stdlib)直读 Info.plist,不逐个 spawn 进程。
    覆盖用户目录 + 系统目录 + 嵌套子目录(如 Adobe/Setapp),尽量不漏掉有用量的 App。"""
    import plistlib
    bases = APP_SCAN_DIRS + [
        str(Path.home() / "Applications"),
        "/Applications/Setapp",
    ]
    seen = set()
    apps = []
    for base in bases:
        for app in _iter_app_bundles(Path(base)):
            plist = app / "Contents" / "Info.plist"
            if not plist.exists():
                continue
            try:
                with open(plist, "rb") as f:
                    info = plistlib.load(f)
            except Exception:  # noqa: BLE001
                continue
            bid = info.get("CFBundleIdentifier")
            if not bid or bid in seen:
                continue
            seen.add(bid)
            apps.append({
                "name": info.get("CFBundleDisplayName")
                or info.get("CFBundleName") or app.stem,
                "bundle_id": bid,
                "path": str(app),
            })
    apps.sort(key=lambda a: a["name"].lower())
    return apps


def app_info_for_bundle(bundle_id: str) -> "dict | None":
    """按 bundle id 用 mdfind 定位 .app,返回 {"name","bundle_id","path"}。
    用于补全「有用量但不在标准扫描目录」的 App(如系统 App、非常规安装位置)。"""
    try:
        out = subprocess.run(
            ["/usr/bin/mdfind",
             f"kMDItemCFBundleIdentifier == '{bundle_id}'"],
            capture_output=True, text=True, timeout=5
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    path = None
    for line in out.stdout.splitlines():
        line = line.strip()
        if line.endswith(".app"):
            path = line
            break
    if not path:
        return None
    p = Path(path)
    name = p.stem
    plist = p / "Contents" / "Info.plist"
    if plist.exists():
        import plistlib
        try:
            with open(plist, "rb") as f:
                info = plistlib.load(f)
            name = info.get("CFBundleDisplayName") or info.get("CFBundleName") or name
        except Exception:  # noqa: BLE001
            pass
    return {"name": name, "bundle_id": bundle_id, "path": path}


def app_icon_data_uri(bundle_id: str, size: int = 44) -> "str | None":
    """按 bundle id 找到 .app,把它的 .icns 图标转成 size×size 的 PNG data URI。
    供 GUI 懒加载单个应用图标(不塞进 list_installed_apps,避免整包过大)。"""
    import plistlib
    import base64
    import tempfile

    target = None
    for a in list_installed_apps():
        if a["bundle_id"] == bundle_id:
            target = Path(a["path"])
            break
    if not target or not target.exists():
        return None
    plist = target / "Contents" / "Info.plist"
    try:
        with open(plist, "rb") as f:
            info = plistlib.load(f)
    except Exception:  # noqa: BLE001
        return None
    icon_name = info.get("CFBundleIconFile") or "AppIcon"
    if not icon_name.endswith(".icns"):
        icon_name += ".icns"
    icns = target / "Contents" / "Resources" / icon_name
    if not icns.exists():
        cand = list((target / "Contents" / "Resources").glob("*.icns"))
        if not cand:
            return None
        icns = cand[0]
    try:
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
            out = tmp.name
        subprocess.run(
            ["/usr/bin/sips", "-z", str(size), str(size), "-s", "format", "png",
             str(icns), "--out", out],
            capture_output=True, timeout=8,
        )
        data = Path(out).read_bytes()
        Path(out).unlink(missing_ok=True)
        if not data:
            return None
        return "data:image/png;base64," + base64.b64encode(data).decode()
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return None


def frontmost_bundle_id() -> "str | None":
    """当前最前台 App 的 bundle id。
    有些多进程 App(如 Edge)用"first process whose frontmost"取 bundle id 会返回
    'missing value',故改用 frontmost application 的名字 -> 再解析 bundle id,并过滤空值。"""
    # 先直接问最前台 process 的 bundle identifier
    script = (
        'tell application "System Events"\n'
        '  set p to first application process whose frontmost is true\n'
        '  set bid to bundle identifier of p\n'
        '  if bid is missing value then return name of p\n'
        '  return bid\n'
        'end tell'
    )
    try:
        out = subprocess.run(["/usr/bin/osascript", "-e", script],
                             capture_output=True, text=True, timeout=5)
        val = out.stdout.strip()
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    if not val or val == "missing value":
        return None
    # 拿到的是 bundle id(含点)直接用;否则是 App 名 -> 解析成 bundle id
    if "." in val:
        return val
    return bundle_id_for_app(val) or None


def enforce_app_lock(bundle_id: str, app_name: str = "", reason: str = "") -> bool:
    """
    受限 App 在前台被使用时:把它藏到后台并把焦点切走(不弹模态框、不 kill、不丢数据)。
    每 ~1 秒的前台快检会持续把它按下去,用户切过去就被弹回,等效"锁住不让用"。
    没在前台/没运行 -> 不动作。返回是否执行了动作。
    """
    running = running_bundle_ids()
    if bundle_id not in running:
        return False
    if frontmost_bundle_id() != bundle_id:
        return False

    # 藏起被锁 App,并把焦点切到访达 —— 用户无法继续操作它;不弹任何需点击的窗口
    script = (
        'tell application "System Events"\n'
        f'  set visible of (every process whose bundle identifier is "{bundle_id}") to false\n'
        'end tell\n'
        'tell application "Finder" to activate'
    )
    try:
        subprocess.run(["/usr/bin/osascript", "-e", script],
                       capture_output=True, text=True, timeout=8)
        return True
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def _osa_str(s: str) -> str:
    """转义放进 AppleScript 字符串字面量的文本。"""
    return s.replace("\\", "\\\\").replace('"', '\\"')


def write_block_signal(bundle_id: str, app_name: str, reason: str) -> None:
    """写遮挡信号,供用户级青锁盾 app 轮询后弹全屏遮罩。原子写(临时文件+rename)。"""
    import json
    import tempfile
    payload = {
        "active": True,
        "bundle_id": bundle_id,
        "app_name": app_name or bundle_id,
        "reason": reason,
        "ts": time.time(),
    }
    try:
        GUARD_HOME.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(GUARD_HOME), suffix=".tmp")
        with os.fdopen(fd, "w") as f:
            json.dump(payload, f, ensure_ascii=False)
        os.replace(tmp, BLOCK_SIGNAL_PATH)
        os.chmod(BLOCK_SIGNAL_PATH, 0o644)
    except OSError:
        pass


def clear_block_signal() -> None:
    """清除遮挡信号(无被挡应用在前台时)。写 active:false 而非删文件,让 app 稳定读到状态。"""
    import json
    import tempfile
    try:
        if BLOCK_SIGNAL_PATH.exists():
            existing = json.loads(BLOCK_SIGNAL_PATH.read_text())
            if not existing.get("active"):
                return  # 已是 inactive,不重复写
        GUARD_HOME.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(GUARD_HOME), suffix=".tmp")
        with os.fdopen(fd, "w") as f:
            json.dump({"active": False, "ts": time.time()}, f)
        os.replace(tmp, BLOCK_SIGNAL_PATH)
        os.chmod(BLOCK_SIGNAL_PATH, 0o644)
    except (OSError, ValueError):
        pass


def app_name_for_bundle(bundle_id: str) -> str:
    """按 bundle id 取显示名(遮挡提示用);查不到就回退 bundle id。"""
    for a in list_installed_apps():
        if a["bundle_id"] == bundle_id:
            return a.get("name") or bundle_id
    return bundle_id


def notify(title: str, message: str) -> None:
    """弹 macOS 通知。"""
    script = f'display notification "{message}" with title "{title}"'
    try:
        subprocess.run(["/usr/bin/osascript", "-e", script],
                       capture_output=True, timeout=5)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass


# ---- 网站屏蔽:hosts 层 ----
def _render_hosts_block(domains: list) -> str:
    lines = [HOSTS_MARK_BEGIN]
    for d in domains:
        lines.append(f"127.0.0.1 {d}")
        lines.append(f"::1 {d}")
        if not d.startswith("www."):
            lines.append(f"127.0.0.1 www.{d}")
            lines.append(f"::1 www.{d}")
    lines.append(HOSTS_MARK_END)
    return "\n".join(lines) + "\n"


def apply_hosts_block(domains: list) -> None:
    """将屏蔽域名写入 /etc/hosts 的托管区块(需 root)。"""
    text = HOSTS_PATH.read_text() if HOSTS_PATH.exists() else ""
    if HOSTS_MARK_BEGIN in text and HOSTS_MARK_END in text:
        pre = text.split(HOSTS_MARK_BEGIN)[0].rstrip("\n")
        post = text.split(HOSTS_MARK_END)[1].lstrip("\n")
        parts = [pre] if pre else []
    else:
        pre = text.rstrip("\n")
        post = ""
        parts = [pre] if pre else []
    block = _render_hosts_block(domains).rstrip("\n") if domains else ""
    new_parts = []
    if pre:
        new_parts.append(pre)
    if block:
        new_parts.append(block)
    if post:
        new_parts.append(post)
    HOSTS_PATH.write_text("\n".join(new_parts) + "\n")
    # 刷新 DNS 缓存
    subprocess.run(["/usr/bin/dscacheutil", "-flushcache"], capture_output=True)
    subprocess.run(["/usr/bin/killall", "-HUP", "mDNSResponder"], capture_output=True)


# ---- 网站屏蔽:PF 防火墙层(防改 hosts 绕过)----
def apply_pf_block(domains: list) -> None:
    """
    写 PF anchor 规则文件并加载(需 root)。
    PF 按 IP 拦截,先解析域名再写 block 规则。改 hosts 无法绕过 PF。
    """
    ips = set()
    for d in domains:
        for name in ({d, f"www.{d}"} if not d.startswith("www.") else {d}):
            try:
                out = subprocess.run(
                    ["/usr/bin/dig", "+short", name],
                    capture_output=True, text=True, timeout=8
                ).stdout
                for line in out.splitlines():
                    line = line.strip()
                    # 只收 IPv4/IPv6 地址行
                    if line and all(c in "0123456789.:abcdefABCDEF" for c in line):
                        ips.add(line)
            except (FileNotFoundError, subprocess.TimeoutExpired):
                pass
    rules = []
    for ip in sorted(ips):
        rules.append(f"block drop out quick to {ip}")
    PF_ANCHOR_FILE.parent.mkdir(parents=True, exist_ok=True)
    PF_ANCHOR_FILE.write_text("\n".join(rules) + "\n" if rules else "")
    # 加载 anchor
    subprocess.run(
        ["/sbin/pfctl", "-a", PF_ANCHOR_NAME, "-f", str(PF_ANCHOR_FILE)],
        capture_output=True
    )
    subprocess.run(["/sbin/pfctl", "-E"], capture_output=True)
