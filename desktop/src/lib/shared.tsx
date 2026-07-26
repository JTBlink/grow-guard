import { useCallback, useEffect, useState } from "react";
import { invoke } from "@tauri-apps/api/core";
import type { GuardStatus, AdminResult } from "../types";

export function inTauri(): boolean {
  return typeof window !== "undefined" && "__TAURI_INTERNALS__" in window;
}

export const FULL_DISK_ACCESS_GUIDE =
  "要显示系统屏幕使用时长，需要授予完全磁盘访问。\n\n" +
  "点击“好”后，请在列表中找到并开启“青锁盾”；如果列表中没有，点击“+”添加 /Applications/青锁盾.app。\n\n" +
  "授权后请按系统提示退出并重新打开青锁盾。";

export async function hasFullDiskAccess(): Promise<boolean> {
  return invoke<boolean>("has_full_disk_access");
}

export async function openFullDiskAccessSettings(): Promise<void> {
  await invoke("open_full_disk_access_settings");
}

// macOS WKWebView(wry)不实现 JS 的 alert/confirm/prompt 面板,window.prompt() 会直接返回 null,
// 导致所有"输入密码"操作静默失败。因此这里用应用内 React 弹窗替代原生对话框。
export type PromptOpts = { message: string; password?: boolean; confirmMessage?: string };
export type DialogState =
  | { kind: "alert"; message: string; resolve: () => void }
  | { kind: "prompt"; opts: PromptOpts; resolve: (v: string | null) => void };

export function useDialog() {
  const [state, setState] = useState<DialogState | null>(null);
  const alert = useCallback(
    (message: string) => new Promise<void>((resolve) => setState({ kind: "alert", message, resolve })),
    [],
  );
  const prompt = useCallback(
    (opts: PromptOpts) =>
      new Promise<string | null>((resolve) => setState({ kind: "prompt", opts, resolve })),
    [],
  );
  const close = useCallback(() => setState(null), []);
  return { state, alert, prompt, close };
}

export function useStatus() {
  const [status, setStatus] = useState<GuardStatus | null>(null);
  const [systemUsage, setSystemUsage] = useState<Record<string, number> | null>(null);
  const [fullDiskAccess, setFullDiskAccess] = useState<boolean | null>(null);
  const [error, setError] = useState<string | null>(null);
  const refresh = useCallback(async () => {
    if (!inTauri()) {
      setError("请在青锁盾应用窗口中打开，而不是浏览器。运行 grow-guard dev 会自动弹出应用窗口。");
      return;
    }
    try {
      const raw = await invoke<string>("guard_status");
      const s = JSON.parse(raw) as GuardStatus;
      // 先由 App 本体确认 FDA，再读 knowledgeC；权限缺失和“今天确实无用量”不再混为一谈。
      let hasAccess = false;
      try {
        hasAccess = await hasFullDiskAccess();
      } catch {
        hasAccess = false;
      }
      setFullDiskAccess(hasAccess);
      if (hasAccess) {
        try {
          const usageRaw = await invoke<string>("system_usage");
          const usage = JSON.parse(usageRaw) as Record<string, number>;
          setSystemUsage(usage);
          s.usage_source = "knowledgeC";
          for (const a of s.apps) {
            if (usage[a.bundle_id] != null) a.used_min = usage[a.bundle_id];
          }
        } catch {
          setSystemUsage(null);
        }
      } else {
        setSystemUsage(null);
        s.usage_source = "poll";
      }
      setStatus(s);
      setError(null);
    } catch (e) {
      setError(String(e));
    }
  }, []);
  useEffect(() => {
    refresh();
    const id = setInterval(refresh, 5000);
    window.addEventListener("focus", refresh);
    return () => {
      clearInterval(id);
      window.removeEventListener("focus", refresh);
    };
  }, [refresh]);
  return { status, systemUsage, fullDiskAccess, error, refresh };
}

export type AlertFn = (message: string) => Promise<void>;
// 提权执行器。needsPassword=false 用于「加限制」操作(禁用/限额/屏蔽/开时间窗),不问密码;
// 「放松限制」操作(解禁/解锁/删限制)默认要密码,一次输入整会话复用,错了才清缓存重问。
export type ExecAdmin = (
  args: string[],
  opts?: { needsPassword?: boolean },
) => Promise<AdminResult | null>;

// 屏幕用量文案：按小时、分钟、秒拆分，只展示大于 0 的单位。
export function fmtUsage(min: number): string {
  if (!Number.isFinite(min) || min <= 0) return "";
  const totalSeconds = Math.round(min * 60);
  const hours = Math.floor(totalSeconds / 3600);
  const minutes = Math.floor((totalSeconds % 3600) / 60);
  const seconds = totalSeconds % 60;
  return [
    hours > 0 ? `${hours} 小时` : "",
    minutes > 0 ? `${minutes} 分钟` : "",
    seconds > 0 ? `${seconds} 秒` : "",
  ]
    .filter(Boolean)
    .join(" ");
}

export function isBadPassword(output: string): boolean {
  return output.includes("密码错误") || output.includes("密码校验失败");
}

export async function runAdmin(
  args: string[],
  password?: string,
  newPassword?: string,
): Promise<AdminResult> {
  return invoke<AdminResult>("guard_admin", {
    args,
    password: password ?? null,
    newPassword: newPassword ?? null,
  });
}

export async function openTerminal(): Promise<void> {
  await invoke("open_terminal");
}

export async function installCli(): Promise<string> {
  return invoke<string>("install_cli");
}

export async function cliLinkStatus(): Promise<boolean> {
  return invoke<boolean>("cli_link_status");
}

export function Row({ k, v }: { k: string; v: string }) {
  return (
    <div className="kv">
      <span className="k">{k}</span>
      <span className="v">{v}</span>
    </div>
  );
}
