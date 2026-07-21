// GrowGuard Tauri 后端桥接层
//
// 复用现有 Python 核心:所有能力通过调用 grow-guard CLI 实现。
// - 只读(status / 列 App):直接跑 CLI,无需提权。
// - 放松限制(limit/block/unlock/…):经 osascript 弹系统授权框以 root 执行;
//   家长密码经环境变量 GROW_GUARD_PW 传入,不出现在进程 argv。
//
// 开发期 CLI 路径默认取仓库内 ../../backend/cli.py;安装后可用环境变量
// GROW_GUARD_CLI 覆盖为 /Library/Application Support/GrowGuard/backend/cli.py。

use std::path::PathBuf;
use std::process::Command;

use serde::Serialize;

pub mod logger;
use tauri::menu::{MenuBuilder, MenuItemBuilder, SubmenuBuilder};
use tauri::tray::{TrayIconBuilder, TrayIconEvent};
use tauri::{Emitter, Manager, WindowEvent};

#[derive(Serialize)]
struct CliResult {
    ok: bool,
    output: String,
    cancelled: bool,
}

fn cli_path() -> PathBuf {
    if let Ok(p) = std::env::var("GROW_GUARD_CLI") {
        return PathBuf::from(p);
    }
    // 开发构建优先用仓库源码,改后端不必每次重装;发布构建才走 root 独占的已安装副本。
    let repo = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../backend/cli.py");
    if cfg!(debug_assertions) && repo.exists() {
        return repo;
    }
    let installed = PathBuf::from("/Library/Application Support/GrowGuard/backend/cli.py");
    if installed.exists() {
        return installed;
    }
    repo
}

fn python_bin() -> String {
    std::env::var("GROW_GUARD_PYTHON").unwrap_or_else(|_| "python3".to_string())
}

// ── 全屏遮罩:root 守护进程写 block_signal.json,这里轮询驱动一个置顶全屏遮罩窗 ──
// root 守护进程画不出窗(会话隔离),故遮罩必须由用户级的本 app 画。

fn block_signal_path() -> PathBuf {
    if let Ok(p) = std::env::var("GROW_GUARD_HOME") {
        return PathBuf::from(p).join("block_signal.json");
    }
    PathBuf::from("/Library/Application Support/GrowGuard/data/block_signal.json")
}

fn data_dir() -> PathBuf {
    if let Ok(p) = std::env::var("GROW_GUARD_HOME") {
        return PathBuf::from(p);
    }
    PathBuf::from("/Library/Application Support/GrowGuard/data")
}

fn read_json(path: &std::path::Path) -> Option<serde_json::Value> {
    let text = std::fs::read_to_string(path).ok()?;
    serde_json::from_str(&text).ok()
}

// App 自己读 config.json(0644 世界可读)判断某 App 是否被锁,不依赖 root 守护进程的信号 ——
// 因为 root 守护跑在系统上下文,查用户 GUI 前台不可靠(会得到 missing value);而本 app 在
// 用户会话里靠 NSWorkspace 能精确拿到刚切前台的 App。故遮罩判定放在这里最可靠。
// 返回 Some(reason) 表示该 App 应被遮挡;None 表示放行。
fn lock_reason_for(bundle_id: &str) -> Option<String> {
    let dir = data_dir();
    let cfg = read_json(&dir.join("config.json"))?;

    // 宽限期内一律放行
    let now = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_secs_f64())
        .unwrap_or(0.0);
    let grace_until = cfg
        .get("grace")
        .and_then(|g| g.get("until_ts"))
        .and_then(|x| x.as_f64())
        .unwrap_or(0.0);
    if now < grace_until {
        return None;
    }

    // 时间窗:启用且当前不在允许时段 -> 所有受限 App 锁定
    let out_of_window = is_out_of_window(&cfg);

    let rule = cfg.get("apps").and_then(|a| a.get(bundle_id));
    let Some(rule) = rule else {
        // 未受管控的 App:仅当时间窗外时不影响(时间窗只锁受限 App)
        return None;
    };
    if rule.get("blocked").and_then(|x| x.as_bool()).unwrap_or(false) {
        return Some("该应用已被禁用".to_string());
    }
    if out_of_window {
        return Some("当前不在允许使用时段".to_string());
    }
    // 时长上限:读 state.json 今日用量,>= 上限则锁。
    // 关键:只有 state.date == 今天 才采用其用量;否则(跨天未重置/守护未写)视为今日 0min,
    // 避免拿昨天的旧用量把今天误锁(此前 "没到量也锁了" 的根因)。
    if let Some(limit) = rule.get("daily_limit_min").and_then(|x| x.as_f64()) {
        let used = read_used_min(&dir, bundle_id);
        if used >= limit {
            return Some(format!(
                "今日使用时长已用完(上限 {:.0} 分钟,已用 {:.1} 分钟)",
                limit, used
            ));
        }
    }
    None
}

// 读取某 App 今日已用分钟数。只有 state.date == 今天 才采用其用量,
// 否则(跨天未重置 / 守护未写)视为今日 0min —— 与 lock_reason_for 的判定口径一致,
// 避免拿昨天的旧用量误判。
fn read_used_min(dir: &std::path::Path, bundle_id: &str) -> f64 {
    let state = read_json(&dir.join("state.json"));
    let state_date = state
        .as_ref()
        .and_then(|s| s.get("date").and_then(|x| x.as_str()))
        .unwrap_or("");
    if state_date != local_today() {
        return 0.0;
    }
    state
        .as_ref()
        .and_then(|s| {
            s.get("usage_min")
                .and_then(|u| u.get(bundle_id))
                .and_then(|x| x.as_f64())
        })
        .unwrap_or(0.0)
}

// 切换焦点时打印该 App 今日使用时长(不依赖是否设了上限,始终打印),便于实时观察用量。
fn log_usage_on_focus(bundle_id: &str, app_name: &str) {
    let dir = data_dir();
    let used = read_used_min(&dir, bundle_id);
    let limit = read_json(&dir.join("config.json"))
        .and_then(|cfg| {
            cfg.get("apps")
                .and_then(|a| a.get(bundle_id))
                .and_then(|r| r.get("daily_limit_min"))
                .and_then(|x| x.as_f64())
        });
    match limit {
        Some(lim) => glog!(
            "[用量] {app_name} ({bundle_id}) 今日已用 {:.1} 分钟 / 上限 {:.0} 分钟 ({})",
            used,
            lim,
            if used >= lim { "已达上限" } else { "未达上限" }
        ),
        None => glog!(
            "[用量] {app_name} ({bundle_id}) 今日已用 {:.1} 分钟 / 未设上限",
            used
        ),
    }
}

// 本地当天日期 YYYY-MM-DD,与 backend/core.py 的 date.today().isoformat() 对齐。
fn local_today() -> String {
    #[cfg(target_os = "macos")]
    {
        use chrono::Local;
        Local::now().format("%Y-%m-%d").to_string()
    }
    #[cfg(not(target_os = "macos"))]
    {
        String::new()
    }
}

fn is_out_of_window(cfg: &serde_json::Value) -> bool {
    let sch = match cfg.get("schedule") {
        Some(s) => s,
        None => return false,
    };
    if !sch.get("enabled").and_then(|x| x.as_bool()).unwrap_or(false) {
        return false;
    }
    let parse_hm = |k: &str| -> Option<u32> {
        let s = sch.get(k)?.as_str()?;
        let (h, m) = s.split_once(':')?;
        Some(h.trim().parse::<u32>().ok()? * 60 + m.trim().parse::<u32>().ok()?)
    };
    let (Some(start), Some(end)) = (parse_hm("allow_start"), parse_hm("allow_end")) else {
        return false;
    };
    #[cfg(target_os = "macos")]
    let now_min = {
        use chrono::{Local, Timelike};
        let n = Local::now();
        n.hour() * 60 + n.minute()
    };
    #[cfg(not(target_os = "macos"))]
    let now_min = 0u32;
    let inside = if start <= end {
        now_min >= start && now_min < end
    } else {
        now_min >= start || now_min < end
    };
    !inside
}

#[derive(Serialize, Clone, Default)]
struct BlockInfo {
    active: bool,
    app_name: String,
    reason: String,
}

fn read_block_signal() -> BlockInfo {
    let path = block_signal_path();
    let Ok(text) = std::fs::read_to_string(&path) else {
        return BlockInfo::default();
    };
    let Ok(v) = serde_json::from_str::<serde_json::Value>(&text) else {
        return BlockInfo::default();
    };
    BlockInfo {
        active: v.get("active").and_then(|x| x.as_bool()).unwrap_or(false),
        app_name: v
            .get("app_name")
            .and_then(|x| x.as_str())
            .unwrap_or("")
            .to_string(),
        reason: v
            .get("reason")
            .and_then(|x| x.as_str())
            .unwrap_or("")
            .to_string(),
    }
}

const OVERLAY_LABEL: &str = "overlay";

fn show_overlay(app: &tauri::AppHandle, info: &BlockInfo) {
    let win = match app.get_webview_window(OVERLAY_LABEL) {
        Some(w) => w,
        None => {
            let built = tauri::WebviewWindowBuilder::new(
                app,
                OVERLAY_LABEL,
                tauri::WebviewUrl::App("overlay.html".into()),
            )
            .title("青锁盾")
            .decorations(false)
            .always_on_top(true)
            .skip_taskbar(true)
            .visible(false)
            .build();
            match built {
                Ok(w) => w,
                Err(_) => return,
            }
        }
    };
    let _ = app.emit_to(OVERLAY_LABEL, "overlay://block", info.clone());
    // 不用原生 set_fullscreen(会走 macOS 独立空间、有 ~1s 动画且能被 Cmd-Tab 绕过)。
    // 改为:无边框窗铺满整块屏幕(含菜单栏区域)+ 置顶,瞬时覆盖、无动画。
    if let Ok(Some(monitor)) = win.current_monitor().or_else(|_| app.primary_monitor()) {
        let pos = monitor.position();
        let size = monitor.size();
        let _ = win.set_position(tauri::PhysicalPosition::new(pos.x, pos.y));
        let _ = win.set_size(tauri::PhysicalSize::new(size.width, size.height));
    }
    let _ = win.set_always_on_top(true);
    let _ = win.show();
    let _ = win.set_focus();
    cover_menu_bar(&win);
}

// macOS:把遮罩窗的层级抬到菜单栏之上,并让它出现在所有 Space,真正铺满整屏。
#[cfg(target_os = "macos")]
fn cover_menu_bar(win: &tauri::WebviewWindow) {
    use objc2::runtime::AnyObject;
    use objc2::msg_send;
    let Ok(ns) = win.ns_window() else { return };
    let ns = ns as *mut AnyObject;
    unsafe {
        // NSScreenSaverWindowLevel(1000) 之上,盖住菜单栏与 Dock
        let level: i64 = 1000;
        let _: () = msg_send![ns, setLevel: level];
        // NSWindowCollectionBehaviorCanJoinAllSpaces(1) | FullScreenAuxiliary(1<<8)
        let behavior: u64 = 1 | (1 << 8);
        let _: () = msg_send![ns, setCollectionBehavior: behavior];
        let _: () = msg_send![ns, setHidesOnDeactivate: false];
    }
}

#[cfg(not(target_os = "macos"))]
fn cover_menu_bar(_win: &tauri::WebviewWindow) {}

// 主窗口不随失活隐藏:点「解锁」等操作会弹出系统授权框(SecurityAgent 进程)抢焦点,
// 本 app 是 Accessory(无 Dock),失活时 macOS 默认会把窗口藏起来 —— 授权框一弹主窗就消失。
// 关掉 hidesOnDeactivate 让主窗在授权框浮起时仍留在原处。
#[cfg(target_os = "macos")]
fn keep_main_visible(win: &tauri::WebviewWindow) {
    use objc2::msg_send;
    use objc2::runtime::AnyObject;
    let Ok(ns) = win.ns_window() else { return };
    let ns = ns as *mut AnyObject;
    unsafe {
        let _: () = msg_send![ns, setHidesOnDeactivate: false];
    }
}

#[cfg(not(target_os = "macos"))]
fn keep_main_visible(_win: &tauri::WebviewWindow) {}

fn hide_overlay(app: &tauri::AppHandle) {
    if let Some(w) = app.get_webview_window(OVERLAY_LABEL) {
        let _ = w.hide();
    }
}

// 遮罩判定统一入口:给定当前前台 App(bundle_id + 名称),App 自己读 config 判断是否遮挡。
// 用全局 AtomicBool 记住当前是否已显示,避免重复 show/hide。
static OVERLAY_SHOWING: std::sync::atomic::AtomicBool = std::sync::atomic::AtomicBool::new(false);

fn is_guard_app(bundle_id: &str, app_name: &str) -> bool {
    bundle_id == "com.jtstudio.growguard"
        || matches!(app_name, "grow-guard-gui" | "青锁盾")
}

fn focus_log_target(bundle_id: &str, app_name: &str) -> String {
    if bundle_id.is_empty() && is_guard_app(bundle_id, app_name) {
        return "dev".to_string();
    }
    bundle_id.to_string()
}

fn evaluate_and_apply(app: &tauri::AppHandle, bundle_id: &str, app_name: &str, from_focus: bool) {
    use std::sync::atomic::Ordering;
    let showing = OVERLAY_SHOWING.load(Ordering::Relaxed);
    // 自己(遮罩窗/主窗)在前台时不动,避免遮罩抢焦点后又把自己判成"未锁"而隐藏 -> 抖动
    if is_guard_app(bundle_id, app_name) {
        if bundle_id.is_empty() {
            glog!("[焦点事件] 识别为青锁盾自身开发态窗口: {app_name} (dev)");
        }
        return;
    }
    // 焦点切换触发时打印当前 App 今日用量;轮询兜底(800ms)不打,避免刷屏。
    if from_focus {
        log_usage_on_focus(bundle_id, app_name);
    }
    match lock_reason_for(bundle_id) {
        Some(reason) => {
            glog!("[遮挡] {app_name} ({bundle_id}) 命中锁定:{reason} -> 显示全屏遮罩");
            let info = BlockInfo {
                active: true,
                app_name: app_name.to_string(),
                reason,
            };
            show_overlay(app, &info);
            OVERLAY_SHOWING.store(true, Ordering::Relaxed);
        }
        None => {
            if showing {
                glog!("[遮挡] {app_name} ({bundle_id}) 未锁 -> 隐藏遮罩");
                hide_overlay(app);
                OVERLAY_SHOWING.store(false, Ordering::Relaxed);
            }
        }
    }
}

// 取当前前台 App 的 (bundle_id, 名称)。用 NSWorkspace(用户会话内,可靠),不用 osascript。
#[cfg(target_os = "macos")]
fn frontmost_app() -> Option<(String, String)> {
    use objc2_app_kit::NSWorkspace;
    let ws = NSWorkspace::sharedWorkspace();
    let running = ws.frontmostApplication()?;
    let bid = running.bundleIdentifier().map(|s| s.to_string())?;
    let name = running
        .localizedName()
        .map(|s| s.to_string())
        .unwrap_or_default();
    Some((bid, name))
}

#[cfg(not(target_os = "macos"))]
fn frontmost_app() -> Option<(String, String)> {
    None
}

fn apply_overlay_state(app: &tauri::AppHandle) {
    if let Some((bid, name)) = frontmost_app() {
        evaluate_and_apply(app, &bid, &name, false);
    }
}

fn spawn_overlay_watcher(app: tauri::AppHandle) {
    std::thread::spawn(move || loop {
        let a = app.clone();
        let _ = app.run_on_main_thread(move || apply_overlay_state(&a));
        std::thread::sleep(std::time::Duration::from_millis(800));
    });
}

// 焦点事件驱动:切到任意 App 前台的一瞬间(NSWorkspace 原生通知)立即判定遮罩,近零延迟。
// App 自己读 config(0644 世界可读)判定 —— 不依赖 root 守护写信号(它在系统上下文查前台不可靠)。
// 轮询(800ms)保留为兜底:错过事件或用量到点时也能纠正。
#[cfg(target_os = "macos")]
fn spawn_focus_observer(app: tauri::AppHandle) {
    use block2::RcBlock;
    use objc2_app_kit::{
        NSRunningApplication, NSWorkspace, NSWorkspaceApplicationKey,
        NSWorkspaceDidActivateApplicationNotification,
    };
    use objc2_foundation::NSNotification;
    use std::ptr::NonNull;

    let workspace = NSWorkspace::sharedWorkspace();
    let center = workspace.notificationCenter();
    let app_for_block = app.clone();

    let block = RcBlock::new(move |note: NonNull<NSNotification>| {
        // 从通知的 userInfo 取刚切到前台的 App(NSRunningApplication)
        let note = unsafe { note.as_ref() };
        let mut bid = String::new();
        let mut name = String::new();
        if let Some(info) = note.userInfo() {
            let key = unsafe { NSWorkspaceApplicationKey };
            if let Some(obj) = info.objectForKey(key) {
                if let Ok(running) = obj.downcast::<NSRunningApplication>() {
                    bid = running.bundleIdentifier().map(|s| s.to_string()).unwrap_or_default();
                    name = running.localizedName().map(|s| s.to_string()).unwrap_or_default();
                }
            }
        }
        let bid_for_log = focus_log_target(&bid, &name);
        glog!("[焦点事件] 切到前台: {name} ({bid_for_log})");
        let a = app_for_block.clone();
        let _ = app_for_block.run_on_main_thread(move || {
            if bid.is_empty() {
                apply_overlay_state(&a);
            } else {
                evaluate_and_apply(&a, &bid, &name, true);
            }
        });
    });

    let token = unsafe {
        center.addObserverForName_object_queue_usingBlock(
            Some(NSWorkspaceDidActivateApplicationNotification),
            None,
            None,
            &block,
        )
    };
    // observer token 必须活到进程结束,否则通知会被注销 —— 直接 leak。
    std::mem::forget(token);
    glog!("[焦点监听] NSWorkspace 前台切换监听已注册");
}

#[cfg(not(target_os = "macos"))]
fn spawn_focus_observer(_app: tauri::AppHandle) {}

// shell 单引号转义(拼进 osascript 的 do shell script)
fn shq(s: &str) -> String {
    format!("'{}'", s.replace('\'', "'\\''"))
}

fn osa_esc(s: &str) -> String {
    s.replace('\\', "\\\\").replace('"', "\\\"")
}

#[tauri::command]
fn guard_status() -> Result<String, String> {
    let out = Command::new(python_bin())
        .arg(cli_path())
        .arg("status")
        .arg("--json")
        .output()
        .map_err(|e| format!("无法执行 CLI: {e}"))?;
    if !out.status.success() {
        return Err(String::from_utf8_lossy(&out.stderr).into_owned());
    }
    Ok(String::from_utf8_lossy(&out.stdout).into_owned())
}

#[tauri::command]
fn list_apps() -> Result<String, String> {
    let out = Command::new(python_bin())
        .arg(cli_path())
        .arg("list-apps")
        .arg("--json")
        .output()
        .map_err(|e| format!("无法执行 CLI: {e}"))?;
    if !out.status.success() {
        return Err(String::from_utf8_lossy(&out.stderr).into_owned());
    }
    Ok(String::from_utf8_lossy(&out.stdout).into_owned())
}

// App 图标:Rust 本体直读 .app 的 .icns,sips 转 44px PNG data URI。
// 前端传 .app 路径(list_apps 已带 path),避免 python 全量扫描,快很多。
#[tauri::command]
fn app_icon(app_path: String) -> Result<String, String> {
    use std::io::Read;
    let app = PathBuf::from(&app_path);
    let res = app.join("Contents/Resources");
    // Info.plist 的 CFBundleIconFile 优先,否则取目录里第一个 .icns
    let icns = plist_icon(&app).or_else(|| first_icns(&res));
    let icns = match icns {
        Some(p) => p,
        None => return Ok(String::new()),
    };
    let tmp = std::env::temp_dir().join(format!("gg-icon-{}.png", std::process::id()));
    let ok = Command::new("/usr/bin/sips")
        .args(["-z", "44", "44", "-s", "format", "png"])
        .arg(&icns)
        .arg("--out")
        .arg(&tmp)
        .output()
        .map(|o| o.status.success())
        .unwrap_or(false);
    if !ok {
        return Ok(String::new());
    }
    let mut buf = Vec::new();
    if std::fs::File::open(&tmp)
        .and_then(|mut f| f.read_to_end(&mut buf))
        .is_err()
    {
        return Ok(String::new());
    }
    let _ = std::fs::remove_file(&tmp);
    if buf.is_empty() {
        return Ok(String::new());
    }
    Ok(format!("data:image/png;base64,{}", b64(&buf)))
}

fn plist_icon(app: &std::path::Path) -> Option<PathBuf> {
    let plist = app.join("Contents/Info.plist");
    let out = Command::new("/usr/libexec/PlistBuddy")
        .args(["-c", "Print :CFBundleIconFile"])
        .arg(&plist)
        .output()
        .ok()?;
    if !out.status.success() {
        return None;
    }
    let mut name = String::from_utf8_lossy(&out.stdout).trim().to_string();
    if name.is_empty() {
        return None;
    }
    if !name.ends_with(".icns") {
        name.push_str(".icns");
    }
    let p = app.join("Contents/Resources").join(name);
    p.exists().then_some(p)
}

fn first_icns(res: &std::path::Path) -> Option<PathBuf> {
    std::fs::read_dir(res)
        .ok()?
        .filter_map(|e| e.ok())
        .map(|e| e.path())
        .find(|p| p.extension().map(|x| x == "icns").unwrap_or(false))
}

// 极简 base64 编码(标准表),避免引额外依赖
fn b64(data: &[u8]) -> String {
    const T: &[u8; 64] = b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
    let mut out = String::with_capacity((data.len() + 2) / 3 * 4);
    for chunk in data.chunks(3) {
        let b = [
            chunk[0],
            *chunk.get(1).unwrap_or(&0),
            *chunk.get(2).unwrap_or(&0),
        ];
        let n = ((b[0] as u32) << 16) | ((b[1] as u32) << 8) | (b[2] as u32);
        out.push(T[((n >> 18) & 63) as usize] as char);
        out.push(T[((n >> 12) & 63) as usize] as char);
        out.push(if chunk.len() > 1 { T[((n >> 6) & 63) as usize] as char } else { '=' });
        out.push(if chunk.len() > 2 { T[(n & 63) as usize] as char } else { '=' });
    }
    out
}

// 系统用量:Rust 本体直读 knowledgeC(而非 spawn python),
// 这样发起 TCC 调用的是青锁盾.app 自己 -> FDA 授权面板显示"青锁盾"而非 python3。
// 返回 {bundle_id: 今日分钟} 的 JSON;无 FDA/读取失败返回 "{}"。
#[tauri::command]
fn system_usage() -> Result<String, String> {
    let db = knowledgec_path();
    let db = match db {
        Some(p) => p,
        None => return Ok("{}".into()),
    };
    let mac_epoch = 978_307_200i64;
    // 本地时区的今日零点(不能用 UTC 对齐,否则东八区会差 8 小时漏掉早上的用量)
    use chrono::{Datelike, Local, TimeZone, Timelike};
    let now = Local::now();
    let local_midnight = Local
        .with_ymd_and_hms(now.year(), now.month(), now.day(), 0, 0, 0)
        .single()
        .map(|dt| dt.timestamp())
        .unwrap_or_else(|| now.timestamp() - (now.num_seconds_from_midnight() as i64));
    let start_of_day = local_midnight - mac_epoch;
    let uri = format!("file:{}?mode=ro&immutable=1", db.to_string_lossy());
    let conn = match rusqlite::Connection::open_with_flags(
        &uri,
        rusqlite::OpenFlags::SQLITE_OPEN_READ_ONLY | rusqlite::OpenFlags::SQLITE_OPEN_URI,
    ) {
        Ok(c) => c,
        Err(_) => return Ok("{}".into()),
    };
    let sql = "SELECT ZVALUESTRING, SUM(ZENDDATE - ZSTARTDATE) FROM ZOBJECT \
               WHERE ZSTREAMNAME = '/app/usage' AND ZSTARTDATE >= ?1 GROUP BY ZVALUESTRING";
    let mut map = serde_json::Map::new();
    if let Ok(mut stmt) = conn.prepare(sql) {
        let rows = stmt.query_map([start_of_day as f64], |r| {
            Ok((r.get::<_, Option<String>>(0)?, r.get::<_, Option<f64>>(1)?))
        });
        if let Ok(rows) = rows {
            for row in rows.flatten() {
                if let (Some(bid), Some(secs)) = row {
                    if !bid.is_empty() && secs > 0.0 {
                        let mins = (secs / 60.0 * 10.0).round() / 10.0;
                        map.insert(bid, serde_json::json!(mins));
                    }
                }
            }
        }
    }
    Ok(serde_json::Value::Object(map).to_string())
}

fn knowledgec_path() -> Option<PathBuf> {
    let sys = PathBuf::from("/private/var/db/CoreDuet/Knowledge/knowledgeC.db");
    if sys.exists() {
        return Some(sys);
    }
    let user = dirs::home_dir()?.join("Library/Application Support/Knowledge/knowledgeC.db");
    user.exists().then_some(user)
}

// 放松限制类操作:经 osascript 提权执行,密码走环境变量不进 argv
// new_password:仅 install 首次初始化时用,经 GROW_GUARD_NEWPW 传入设置家长主密码。
#[tauri::command]
fn guard_admin(
    args: Vec<String>,
    password: Option<String>,
    new_password: Option<String>,
) -> Result<CliResult, String> {
    let mut env_prefix = String::new();
    if let Some(pw) = &password {
        env_prefix.push_str(&format!("GROW_GUARD_PW={} ", shq(pw)));
    }
    if let Some(npw) = &new_password {
        env_prefix.push_str(&format!("GROW_GUARD_NEWPW={} ", shq(npw)));
    }
    let py = python_bin();
    let cli = cli_path();
    let mut parts: Vec<String> = vec![shq(&py), shq(&cli.to_string_lossy())];
    for a in &args {
        parts.push(shq(a));
    }
    let inner = format!("env {}{}", env_prefix, parts.join(" "));
    let script = format!(
        "do shell script \"{}\" with administrator privileges",
        osa_esc(&inner)
    );
    let out = Command::new("/usr/bin/osascript")
        .arg("-e")
        .arg(&script)
        .output()
        .map_err(|e| format!("无法执行 osascript: {e}"))?;
    if !out.status.success() {
        let err = String::from_utf8_lossy(&out.stderr).trim().to_string();
        // 用户在系统授权框点了取消:AppleScript 报 -128,静默不当失败
        let cancelled = err.contains("-128") || err.contains("User canceled") || err.contains("用户已取消");
        return Ok(CliResult {
            ok: false,
            output: if err.is_empty() { "操作已取消或失败".into() } else { err },
            cancelled,
        });
    }
    Ok(CliResult {
        ok: true,
        output: String::from_utf8_lossy(&out.stdout).trim().to_string(),
        cancelled: false,
    })
}

fn show_main(app: &tauri::AppHandle) {
    use std::sync::atomic::Ordering;

    hide_overlay(app);
    OVERLAY_SHOWING.store(false, Ordering::Relaxed);
    if let Some(w) = app.get_webview_window("main") {
        let _ = w.show();
        let _ = w.unminimize();
        // 遮罩窗是置顶的;主窗口需临时置顶盖过它,否则点图标看似"打不开"(被遮罩压住)。
        // 家长点图标就是要来管理/解锁,故让主窗口浮到最前并抢焦点。
        let _ = w.set_always_on_top(true);
        let _ = w.set_focus();
        let _ = w.set_always_on_top(false);
    }
}

fn hide_main(app: &tauri::AppHandle) {
    glog!("[窗口] 隐藏主窗口到状态栏");
    if let Some(w) = app.get_webview_window("main") {
        let _ = w.hide();
    }
}

// dev 模式跑的是未打包的裸二进制(名为 grow-guard-gui),macOS 会拿进程名当程序坞悬停/菜单栏名。
// 打包后的 .app 由 CFBundleName 决定,不受影响;这里只为让 `tauri dev` 也显示中文名。
#[cfg(target_os = "macos")]
fn set_macos_app_name(name: &str) {
    use objc2_foundation::{NSProcessInfo, NSString};
    let info = NSProcessInfo::processInfo();
    info.setProcessName(&NSString::from_str(name));
}

#[cfg(target_os = "macos")]
fn hide_dock_icon() {
    use objc2::MainThreadMarker;
    use objc2_app_kit::{NSApplication, NSApplicationActivationPolicy};

    let mtm = MainThreadMarker::new().expect("macOS app setup should run on main thread");
    let app = NSApplication::sharedApplication(mtm);
    let _ = app.setActivationPolicy(NSApplicationActivationPolicy::Accessory);
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    #[cfg(target_os = "macos")]
    {
        set_macos_app_name("青锁盾");
        hide_dock_icon();
    }

    tauri::Builder::default()
        .plugin(tauri_plugin_opener::init())
        .invoke_handler(tauri::generate_handler![
            guard_status,
            list_apps,
            app_icon,
            system_usage,
            guard_admin
        ])
        .setup(|app| {
            #[cfg(target_os = "macos")]
            {
                let _ = app.set_dock_visibility(false);
            }
            let handle = app.handle();
            // 遮罩双通道:焦点事件驱动(近零延迟)+ 轮询兜底(错过事件也能 500ms 内纠正)。
            spawn_focus_observer(handle.clone());
            spawn_overlay_watcher(handle.clone());
            let refresh = MenuItemBuilder::new("刷新状态")
                .id("refresh")
                .accelerator("CmdOrCtrl+R")
                .build(app)?;
            let devtools = MenuItemBuilder::new("开发者工具")
                .id("devtools")
                .accelerator("CmdOrCtrl+Alt+I")
                .build(app)?;
            let logs = MenuItemBuilder::new("打开日志目录").id("logs").build(app)?;
            let about = MenuItemBuilder::new("关于青锁盾").id("about").build(app)?;
            let help = SubmenuBuilder::new(app, "帮助")
                .item(&refresh)
                .separator()
                .item(&devtools)
                .item(&logs)
                .separator()
                .item(&about)
                .build()?;
            let menu = MenuBuilder::new(app).item(&help).build()?;
            app.set_menu(menu)?;

            // 状态栏托盘:常驻,左键点图标显示窗口,右键出菜单
            let tray_open = MenuItemBuilder::new("打开控制面板").id("tray_open").build(app)?;
            let tray_quit = MenuItemBuilder::new("退出青锁盾").id("tray_quit").build(app)?;
            let tray_menu = MenuBuilder::new(app)
                .item(&tray_open)
                .separator()
                .item(&tray_quit)
                .build()?;
            // 菜单栏专用单色模板图(圆角方框+挂锁),不用彩色 app 图标(会被 template 压成方块)
            let tray_icon =
                tauri::image::Image::from_bytes(include_bytes!("../icons/tray-icon.png"))?;
            TrayIconBuilder::with_id("main-tray")
                .icon(tray_icon)
                .icon_as_template(true)
                .tooltip("青锁盾 · 青少年访问锁")
                .menu(&tray_menu)
                .show_menu_on_left_click(false)
                .on_menu_event(|app, event| match event.id().0.as_str() {
                    "tray_open" => show_main(app),
                    "tray_quit" => {
                        glog!("[托盘] 点击“退出青锁盾”菜单 -> 改为隐藏主窗口");
                        hide_main(app)
                    }
                    _ => {}
                })
                .on_tray_icon_event(|tray, event| {
                    if let TrayIconEvent::Click { .. } = event {
                        show_main(tray.app_handle());
                    }
                })
                .build(app)?;

            // 关窗口只隐藏到状态栏,不退出(后台守护仍在跑,与此无关)
            if let Some(w) = app.get_webview_window("main") {
                keep_main_visible(&w);
                let handle_for_close = handle.clone();
                w.on_window_event(move |e| {
                    if let WindowEvent::CloseRequested { api, .. } = e {
                        api.prevent_close();
                        hide_main(&handle_for_close);
                    }
                });
            }

            handle.on_menu_event(move |app, event| match event.id().0.as_str() {
                "refresh" => {
                    let _ = app.emit("menu://refresh", ());
                }
                "devtools" => {
                    if let Some(w) = app.get_webview_window("main") {
                        w.open_devtools();
                    }
                }
                "logs" => {
                    let _ = Command::new("/usr/bin/open")
                        .arg("/Library/Application Support/GrowGuard/data")
                        .status();
                }
                "about" => {
                    let _ = app.emit("menu://about", ());
                }
                _ => {}
            });
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("error while building tauri application")
        .run(|app, event| {
            match event {
                // 点 Dock 图标(reopen):把主窗口重新拉到前台
                tauri::RunEvent::Reopen { .. } => show_main(app),
                tauri::RunEvent::ExitRequested { api, .. } => {
                    glog!("[生命周期] 收到 ExitRequested -> 阻止退出并隐藏应用,保持托盘进程常驻");
                    api.prevent_exit();
                    hide_main(app);
                }
                _ => {}
            }
        });
}
