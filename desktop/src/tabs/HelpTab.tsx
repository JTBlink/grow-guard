import { useCallback, useEffect, useRef, useState } from "react";
import { invoke } from "@tauri-apps/api/core";
import { openPath } from "@tauri-apps/plugin-opener";
import { cliLinkStatus, installCli, openTerminal } from "../lib/shared";

const FEATURES: { tab: string; accent: string; desc: string }[] = [
  { tab: "应用时长", accent: "var(--c-apps)", desc: "勾选应用设每日上限或直接禁用，并设定全天使用时段" },
  { tab: "概览", accent: "var(--c-status)", desc: "查看防护状态与今日已限制的应用" },
  { tab: "网站", accent: "var(--c-sites)", desc: "按域名屏蔽网站，hosts + 防火墙双层生效" },
  { tab: "解锁 / 密码", accent: "var(--c-unlock)", desc: "临时放行和修改家长密码" },
];

const FAQS: { q: string; a: string }[] = [
  { q: "用量显示不准？", a: "按「应用时长」页顶部提示授予完全磁盘访问，即可读取系统精确用量。" },
  { q: "到点会强制关闭应用吗？", a: "不会。只会温和锁定、隐藏到后台，不丢数据。" },
  { q: "哪些操作要家长密码？", a: "放松限制才需要——解锁、改限额、卸载。加限制无需密码。" },
];

type DiagnosticLogs = {
  desktop: string[];
  daemon: string[];
};

export function HelpTab() {
  const [diag, setDiag] = useState("");
  const [cliReady, setCliReady] = useState(false);
  const [developerOpen, setDeveloperOpen] = useState(false);
  const [logSource, setLogSource] = useState<keyof DiagnosticLogs>("desktop");
  const [logs, setLogs] = useState<DiagnosticLogs>({ desktop: [], daemon: [] });
  const [logsError, setLogsError] = useState("");
  const [logsLoading, setLogsLoading] = useState(false);
  const logView = useRef<HTMLPreElement>(null);
  const followLogTail = useRef(true);

  useEffect(() => {
    void cliLinkStatus()
      .then(setCliReady)
      .catch(() => setCliReady(false));
  }, []);

  const refreshLogs = useCallback(async () => {
    setLogsLoading(true);
    try {
      setLogs(await invoke<DiagnosticLogs>("diagnostic_logs"));
      setLogsError("");
    } catch (e) {
      setLogsError(`读取日志失败：${e}`);
    } finally {
      setLogsLoading(false);
    }
  }, []);

  useEffect(() => {
    if (!developerOpen) return;
    void refreshLogs();
    const id = window.setInterval(() => void refreshLogs(), 2000);
    return () => window.clearInterval(id);
  }, [developerOpen, refreshLogs]);

  useEffect(() => {
    const el = logView.current;
    if (el && followLogTail.current) el.scrollTop = el.scrollHeight;
  }, [logs, logSource]);

  const openLogs = async () => {
    setDiag("");
    const dir = "/Library/Application Support/GrowGuard/data";
    try {
      await openPath(dir);
    } catch (e) {
      setDiag(`打不开日志目录：${e}。若尚未运行「sudo grow-guard install」起守护进程，日志目录还不存在。`);
    }
  };

  const setupCli = async () => {
    setDiag("");
    try {
      const link = await installCli();
      setCliReady(true);
      setDiag(`命令行已就绪：终端里可直接运行 grow-guard（${link}）。`);
    } catch (e) {
      setDiag(`创建命令行软链接失败：${e}`);
    }
  };

  const launchTerminal = async () => {
    setDiag("");
    try {
      await openTerminal();
      setDiag("已打开终端，并预填 sudo grow-guard 命令。若没弹出，请检查系统是否拦截了 Terminal 自动化。");
    } catch (e) {
      setDiag(`打开命令行失败：${e}`);
    }
  };

  const launchDevtools = async () => {
    try {
      await invoke("open_devtools");
    } catch (e) {
      setLogsError(`打开 DevTools 失败：${e}`);
    }
  };

  const currentLogs = logs[logSource];
  const emptyMessage =
    logSource === "desktop"
      ? "等待焦点切换与限额判定日志…"
      : "暂无守护进程日志，请先确认后台守护已启用。";

  return (
    <section className="help">
      <div className="help-intro">
        <div className="help-intro-title">青锁盾守护孩子的屏幕时间</div>
        <div className="help-intro-sub">应用时长、网站过滤、使用时段，由后台守护进程持续执行；温和锁定，防绕过、防卸载。</div>
      </div>

      <h3>页面导览</h3>
      <div className="help-guide">
        {FEATURES.map((f) => (
          <div key={f.tab} className="help-feature" style={{ ["--rail" as string]: f.accent }}>
            <span className="help-feature-tab">{f.tab}</span>
            <span className="help-feature-desc">{f.desc}</span>
          </div>
        ))}
      </div>

      <h3>常见问题</h3>
      <div className="help-faq">
        {FAQS.map((f) => (
          <div key={f.q} className="help-qa">
            <div className="help-q">{f.q}</div>
            <div className="help-a">{f.a}</div>
          </div>
        ))}
      </div>

      <div className={developerOpen ? "help-dev open" : "help-dev"}>
        <button
          type="button"
          className="help-dev-toggle"
          onClick={() => setDeveloperOpen((open) => !open)}
          aria-expanded={developerOpen}
        >
          <span className="help-dev-mark">&gt;_</span>
          <span className="help-dev-copy">
            <strong>开发者模式</strong>
            <span>查看应用切换、用量判定和后台守护日志</span>
          </span>
          <span className="help-dev-chevron" aria-hidden>{developerOpen ? "−" : "+"}</span>
        </button>

        {developerOpen && (
          <div className="help-dev-body">
            <div className="help-dev-toolbar">
              <div className="help-log-switch" aria-label="日志来源">
                <button
                  type="button"
                  className={logSource === "desktop" ? "active" : ""}
                  onClick={() => {
                    followLogTail.current = true;
                    setLogSource("desktop");
                  }}
                >
                  桌面切换 <span>{logs.desktop.length}</span>
                </button>
                <button
                  type="button"
                  className={logSource === "daemon" ? "active" : ""}
                  onClick={() => {
                    followLogTail.current = true;
                    setLogSource("daemon");
                  }}
                >
                  后台守护 <span>{logs.daemon.length}</span>
                </button>
              </div>
              <span className={logsLoading ? "help-live syncing" : "help-live"}>
                <i /> {logsLoading ? "同步中" : "每 2 秒刷新"}
              </span>
            </div>

            <pre
              ref={logView}
              className="help-log-view"
              aria-label={logSource === "desktop" ? "桌面切换日志" : "后台守护日志"}
              onScroll={(event) => {
                const el = event.currentTarget;
                followLogTail.current =
                  el.scrollHeight - el.scrollTop - el.clientHeight < 24;
              }}
            >
              {logsError || (currentLogs.length > 0 ? currentLogs.join("\n") : emptyMessage)}
            </pre>

            <div className="help-dev-actions">
              <button type="button" className="btn" onClick={launchDevtools}>打开 DevTools</button>
              <button type="button" className="btn" onClick={() => void refreshLogs()} disabled={logsLoading}>立即刷新</button>
              <button type="button" className="btn" onClick={openLogs}>打开日志目录</button>
            </div>
          </div>
        )}
      </div>

      <div className="help-diag">
        <span className="help-diag-text">
          想用命令行管理？{cliReady ? "可直接打开终端并预填 grow-guard 命令。" : "一键创建 grow-guard 软链接到 /usr/local/bin。"}
        </span>
        {cliReady ? (
          <button className="btn" onClick={launchTerminal}>
            打开命令行
          </button>
        ) : (
          <button className="btn" onClick={setupCli}>
            快速安装命令行
          </button>
        )}
      </div>
      {diag && <div className="help-diag-msg">{diag}</div>}
    </section>
  );
}
