import { useEffect, useRef, useState, useCallback } from "react";
import { listen } from "@tauri-apps/api/event";
import { getVersion } from "@tauri-apps/api/app";
import logoUrl from "./assets/logo.png";
import "./App.css";

import { useDialog, useStatus, runAdmin, isBadPassword, ExecAdmin } from "./lib/shared";
import { DialogHost } from "./components/DialogHost";
import { AppsTab } from "./tabs/AppsTab";
import { StatusTab } from "./tabs/StatusTab";
import { SitesTab } from "./tabs/SitesTab";
import { UnlockTab } from "./tabs/UnlockTab";
import { HelpTab } from "./tabs/HelpTab";

export type Tab = "apps" | "status" | "sites" | "unlock" | "help";

export const TABS: { id: Tab; label: string }[] = [
  { id: "apps", label: "应用时长" },
  { id: "status", label: "概览" },
  { id: "sites", label: "网站" },
  { id: "unlock", label: "解锁 / 密码" },
  { id: "help", label: "帮助" },
];

export default function App() {
  const [tab, setTab] = useState<Tab>("apps");
  const [showAbout, setShowAbout] = useState(false);
  const [enabling, setEnabling] = useState(false);
  const { status, error, refresh } = useStatus();
  const { state: dialogState, alert, prompt, close } = useDialog();
  const protectedOn = !!status?.daemon_running;

  // 会话内缓存家长密码:首个提权操作问一次,之后复用;密码错了才清缓存重问。
  const sessionPw = useRef<string | null>(null);
  const execAdmin = useCallback<ExecAdmin>(
    async (args, opts) => {
      const needsPassword = opts?.needsPassword ?? true;
      if (!needsPassword) {
        return runAdmin(args);
      }
      let pw = sessionPw.current;
      if (pw === null) {
        pw = await prompt({ message: "请输入家长密码", password: true });
        if (pw === null) return null;
        sessionPw.current = pw;
      }
      let res = await runAdmin(args, pw);
      if (!res.ok && isBadPassword(res.output)) {
        sessionPw.current = null;
        const retry = await prompt({ message: "密码错误，请重新输入家长密码", password: true });
        if (retry === null) return null;
        sessionPw.current = retry;
        res = await runAdmin(args, retry);
      }
      return res;
    },
    [prompt],
  );

  const enableGuard = useCallback(async () => {
    if (enabling || protectedOn) return;
    let newPassword: string | undefined;
    if (status && !status.initialized) {
      const pw = await prompt({
        message: "首次启用需设置家长主密码（至少 4 位，用于解锁与修改限制）",
        password: true,
        confirmMessage: "再次输入家长主密码",
      });
      if (pw === null) return;
      if (pw.length < 4) {
        await alert("密码太短（至少 4 位）");
        return;
      }
      newPassword = pw;
    }
    setEnabling(true);
    try {
      const res = await runAdmin(["install"], undefined, newPassword);
      if (!res.ok) await alert(`启用失败: ${res.output || "已取消"}`);
      refresh();
    } finally {
      setEnabling(false);
    }
  }, [enabling, protectedOn, status, refresh, prompt, alert]);

  useEffect(() => {
    const unRefresh = listen("menu://refresh", () => refresh());
    const unAbout = listen("menu://about", () => setShowAbout(true));
    return () => {
      unRefresh.then((f) => f());
      unAbout.then((f) => f());
    };
  }, [refresh]);

  return (
    <div className="app">
      <TopBar
        on={protectedOn}
        enabling={enabling}
        onEnable={enableGuard}
        onAbout={() => setShowAbout(true)}
      />

      <nav className="tabs">
        {TABS.map((t) => (
          <button
            key={t.id}
            className={t.id === tab ? "tab active" : "tab"}
            onClick={() => setTab(t.id)}
          >
            {t.label}
          </button>
        ))}
      </nav>

      <main className="panel">
        {error && <div className="error">读取状态失败: {error}</div>}
        {tab === "status" && (
          <StatusTab status={status} onChange={refresh} execAdmin={execAdmin} />
        )}
        {tab === "apps" && (
          <AppsTab
            status={status}
            onChange={refresh}
            alert={alert}
            execAdmin={execAdmin}
            enabling={enabling}
            onEnable={enableGuard}
          />
        )}
        {tab === "sites" && (
          <SitesTab status={status} onChange={refresh} execAdmin={execAdmin} />
        )}
        {tab === "unlock" && (
          <UnlockTab onChange={refresh} alert={alert} prompt={prompt} execAdmin={execAdmin} />
        )}
        {tab === "help" && <HelpTab />}
      </main>

      {showAbout && <AboutDialog on={protectedOn} onClose={() => setShowAbout(false)} />}
      <DialogHost state={dialogState} close={close} />
    </div>
  );
}

function TopBar({
  on,
  enabling,
  onEnable,
  onAbout,
}: {
  on: boolean;
  enabling: boolean;
  onEnable: () => void;
  onAbout: () => void;
}) {
  return (
    <header className="topbar">
      <img
        className={on ? "brand-logo on" : "brand-logo"}
        src={logoUrl}
        alt="青锁盾"
        onClick={onAbout}
        title="关于青锁盾"
      />
      <div className="brand-text" onClick={onAbout}>
        <div className="brand-name">青锁盾</div>
        <div className="brand-sub">GROW GUARD</div>
      </div>
      {on ? (
        <span className="guard-pill on">
          <span className="dot" />
          防护中
        </span>
      ) : (
        <button
          type="button"
          className="guard-pill clickable"
          disabled={enabling}
          onClick={onEnable}
          title="点击启用后台守护进程"
        >
          <span className="dot" />
          {enabling ? "启用中…" : "未启用 · 点击启用"}
        </button>
      )}
    </header>
  );
}

function AboutDialog({ on, onClose }: { on: boolean; onClose: () => void }) {
  const [version, setVersion] = useState("");
  useEffect(() => {
    getVersion().then(setVersion).catch(() => setVersion(""));
  }, []);
  return (
    <div className="modal-scrim" onClick={onClose}>
      <div className="about-card" onClick={(e) => e.stopPropagation()}>
        <img className="about-logo" src={logoUrl} alt="青锁盾" />
        <div className="about-name">青锁盾</div>
        <div className="about-ver">GROW GUARD{version ? ` · v${version}` : ""}</div>
        <p className="about-desc">
          macOS 青少年访问锁 —— 应用时长控制、网站过滤、使用时段管理，
          由后台守护进程持续守护，防绕过、防卸载。
        </p>
        <div className={on ? "about-pill on" : "about-pill"}>
          {on ? "守护进程运行中" : "守护进程未启用"}
        </div>
        <button className="btn primary about-ok" onClick={onClose}>
          好
        </button>
      </div>
    </div>
  );
}
