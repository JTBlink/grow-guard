// 青锁盾 GUI · 统一日志工具
//
// 目标:把散落在各处的裸 eprintln! 收敛成统一格式,与后端 daemon.py 的
// `[YYYY-MM-DD HH:MM:SS] ...` 时间戳风格对齐,便于两端日志交叉排查。
//
// 输出到 stderr(dev 控制台 / 系统日志可见),同时保留最近 300 条在内存中供
// 帮助页的开发者模式查看。不落盘 —— guard.log 由 root 守护进程独占写入,
// GUI 以普通用户身份跑,写不进那个 root:wheel 目录。
//
// 用法:
//   glog!("[焦点事件] 切到前台: {name}");
//   gwarn!("[遮挡] 配置读取失败: {e}");
//   gerror!("无法执行 CLI: {e}");

use std::collections::VecDeque;
use std::sync::{Mutex, OnceLock};

const MAX_RECENT_ENTRIES: usize = 300;
static RECENT_ENTRIES: OnceLock<Mutex<VecDeque<String>>> = OnceLock::new();

fn entries() -> &'static Mutex<VecDeque<String>> {
    RECENT_ENTRIES.get_or_init(|| Mutex::new(VecDeque::with_capacity(MAX_RECENT_ENTRIES)))
}

/// 日志级别,渲染成统一前缀标签。
#[derive(Clone, Copy)]
pub enum Level {
    Info,
    Warn,
    Error,
}

impl Level {
    fn tag(self) -> &'static str {
        match self {
            Level::Info => "INFO",
            Level::Warn => "WARN",
            Level::Error => "ERROR",
        }
    }
}

/// 当前时间戳,格式 `YYYY-MM-DD HH:MM:SS`,与 backend/daemon.py 的 `_log` 对齐。
fn timestamp() -> String {
    #[cfg(target_os = "macos")]
    {
        use chrono::Local;
        Local::now().format("%Y-%m-%d %H:%M:%S").to_string()
    }
    #[cfg(not(target_os = "macos"))]
    {
        // 非 macOS(理论上不构建)无 chrono clock,退化为不带时间戳。
        String::new()
    }
}

/// 统一日志出口:所有宏最终都走这里,保证格式一致、便于将来改成落盘/上报。
pub fn log(level: Level, msg: &str) {
    let ts = timestamp();
    let line = if ts.is_empty() {
        format!("[{}] {}", level.tag(), msg)
    } else {
        format!("[{}] [{}] {}", ts, level.tag(), msg)
    };
    eprintln!("{line}");
    if let Ok(mut recent) = entries().lock() {
        if recent.len() == MAX_RECENT_ENTRIES {
            recent.pop_front();
        }
        recent.push_back(line);
    }
}

pub fn recent(limit: usize) -> Vec<String> {
    let Ok(recent) = entries().lock() else {
        return Vec::new();
    };
    let skip = recent.len().saturating_sub(limit);
    recent.iter().skip(skip).cloned().collect()
}

/// INFO 级日志。参数与 `format!` 一致。
#[macro_export]
macro_rules! glog {
    ($($arg:tt)*) => {
        $crate::logger::log($crate::logger::Level::Info, &format!($($arg)*))
    };
}

/// WARN 级日志。
#[macro_export]
macro_rules! gwarn {
    ($($arg:tt)*) => {
        $crate::logger::log($crate::logger::Level::Warn, &format!($($arg)*))
    };
}

/// ERROR 级日志。
#[macro_export]
macro_rules! gerror {
    ($($arg:tt)*) => {
        $crate::logger::log($crate::logger::Level::Error, &format!($($arg)*))
    };
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn recent_returns_the_formatted_desktop_log_entry() {
        let marker = format!("logger-marker-{}", std::process::id());

        log(Level::Info, &format!("[焦点事件] {marker}"));

        let entries = recent(MAX_RECENT_ENTRIES);
        assert!(entries
            .iter()
            .any(|line| line.contains(&format!("[INFO] [焦点事件] {marker}"))));
    }
}
