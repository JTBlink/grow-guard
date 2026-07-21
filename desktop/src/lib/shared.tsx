import { useCallback, useEffect, useState } from "react";
import { invoke } from "@tauri-apps/api/core";
import type { GuardStatus, AdminResult } from "../types";

export function inTauri(): boolean {
  return typeof window !== "undefined" && "__TAURI_INTERNALS__" in window;
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
  const [error, setError] = useState<string | null>(null);
  const refresh = useCallback(async () => {
    if (!inTauri()) {
      setError("请在青锁盾应用窗口中打开，而不是浏览器。运行 grow-guard dev 会自动弹出应用窗口。");
      return;
    }
    try {
      const raw = await invoke<string>("guard_status");
      const s = JSON.parse(raw) as GuardStatus;
      // App 自己读 knowledgeC(Rust),这样 FDA 面板显示"青锁盾"而非 python
      try {
        const usageRaw = await invoke<string>("system_usage");
        const usage = JSON.parse(usageRaw) as Record<string, number>;
        if (Object.keys(usage).length > 0) {
          s.usage_source = "knowledgeC";
          for (const a of s.apps) {
            if (usage[a.bundle_id] != null) a.used_min = usage[a.bundle_id];
          }
        }
      } catch {
        /* Rust 读不到就用后端给的值 */
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
  return { status, error, refresh };
}

export type AlertFn = (message: string) => Promise<void>;
// 提权执行器。needsPassword=false 用于「加限制」操作(禁用/限额/屏蔽/开时间窗),不问密码;
// 「放松限制」操作(解禁/解锁/删限制)默认要密码,一次输入整会话复用,错了才清缓存重问。
export type ExecAdmin = (
  args: string[],
  opts?: { needsPassword?: boolean },
) => Promise<AdminResult | null>;

// 今日用量文案:不足 1 分钟按秒显示(粒度 6 秒),否则取整到分钟。
export function fmtUsage(min: number): string {
  if (min < 1) return `${Math.round(min * 60)} 秒`;
  return `${min.toFixed(0)} 分钟`;
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

export function Row({ k, v }: { k: string; v: string }) {
  return (
    <div className="kv">
      <span className="k">{k}</span>
      <span className="v">{v}</span>
    </div>
  );
}
