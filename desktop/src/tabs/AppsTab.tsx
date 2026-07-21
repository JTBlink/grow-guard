import { useEffect, useState } from "react";
import { invoke } from "@tauri-apps/api/core";
import { GuardStatus, InstalledApp } from "../types";
import { AlertFn, ExecAdmin, fmtUsage } from "../lib/shared";
import { StatusCard } from "../components/StatusCard";

export function AppsTab({
  status,
  onChange,
  alert,
  execAdmin,
  enabling,
  onEnable,
}: {
  status: GuardStatus | null;
  onChange: () => void;
  alert: AlertFn;
  execAdmin: ExecAdmin;
  enabling: boolean;
  onEnable: () => void;
}) {
  const [apps, setApps] = useState<InstalledApp[]>([]);
  const [filter, setFilter] = useState("");
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [minutes, setMinutes] = useState("60");
  const [busy, setBusy] = useState(false);
  const [icons, setIcons] = useState<Record<string, string>>({});
  const [totalScreenMin, setTotalScreenMin] = useState<number | null>(null);

  // Schedule state
  const [start, setStart] = useState(status?.schedule.allow_start ?? "07:00");
  const [end, setEnd] = useState(status?.schedule.allow_end ?? "21:30");

  useEffect(() => {
    (async () => {
      try {
        const raw = await invoke<string>("list_apps");
        const list = JSON.parse(raw) as InstalledApp[];
        // 用量以 App 本体(有 FDA)直读的 Rust system_usage 为准:python 子进程无 FDA,
        // list-apps 常拿不到 knowledgeC,故大批 App 缺用量。这里覆盖合并,保证全量准确。
        try {
          const usageRaw = await invoke<string>("system_usage");
          const usage = JSON.parse(usageRaw) as Record<string, number>;
          for (const a of list) {
            const u = usage[a.bundle_id];
            if (u != null) a.used_min = u;
          }
          const total = Object.values(usage).reduce((s, m) => s + m, 0);
          setTotalScreenMin(Object.keys(usage).length > 0 ? total : null);
        } catch {
          /* 读不到就沿用 list-apps 自带的值 */
        }
        list.sort(
          (a, b) => (b.used_min ?? 0) - (a.used_min ?? 0) || a.name.localeCompare(b.name),
        );
        setApps(list);
      } catch {
        setApps([]);
      }
    })();
  }, []);

  const shown = apps.filter(
    (a) =>
      !filter ||
      a.name.toLowerCase().includes(filter.toLowerCase()) ||
      a.bundle_id.toLowerCase().includes(filter.toLowerCase()),
  );

  // 懒加载可见应用的图标(Rust 直读 .icns,传 .app 路径;只拉筛选出的前 60 个)
  useEffect(() => {
    let cancelled = false;
    const pending = shown.slice(0, 60).filter((a) => icons[a.bundle_id] === undefined);
    (async () => {
      for (const a of pending) {
        if (cancelled) return;
        try {
          const uri = await invoke<string>("app_icon", { appPath: a.path });
          if (cancelled) return;
          setIcons((prev) => ({ ...prev, [a.bundle_id]: uri || "" }));
        } catch {
          setIcons((prev) => ({ ...prev, [a.bundle_id]: "" }));
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [filter, apps]);

  const toggle = (bid: string) => {
    const next = new Set(selected);
    if (next.has(bid)) {
      next.delete(bid);
    } else {
      next.add(bid);
    }
    setSelected(next);
  };

  const apply = async (mode: "limit" | "block") => {
    if (selected.size === 0) {
      await alert("请先勾选至少一个 App");
      return;
    }
    if (mode === "limit" && !/^\d+$/.test(minutes)) {
      await alert("请输入数字分钟数");
      return;
    }
    setBusy(true);
    try {
      const fails: string[] = [];
      for (const bid of selected) {
        const args = mode === "limit" ? ["limit", bid, minutes] : ["lock-app", bid];
        const r = await execAdmin(args, { needsPassword: false });
        if (r === null) return;
        if (!r.ok) fails.push(r.output || bid);
      }
      if (fails.length > 0) {
        await alert(`操作失败：\n${fails.join("\n")}`);
      } else {
        setSelected(new Set());
      }
      onChange();
    } finally {
      setBusy(false);
    }
  };

  const relock = async () => {
    const r = await execAdmin(["relock"], { needsPassword: false });
    if (r === null) return;
    onChange();
  };

  const enableSchedule = async () => {
    const r = await execAdmin(["schedule", "--start", start, "--end", end], {
      needsPassword: false,
    });
    if (r === null) return;
    onChange();
  };
  const disableSchedule = async () => {
    const r = await execAdmin(["schedule", "--disable"]);
    if (r === null) return;
    onChange();
  };

  return (
    <section className="apps-section">
      <StatusCard
        status={status}
        enabling={enabling}
        totalScreenMin={totalScreenMin}
        onEnable={onEnable}
        onRelock={relock}
      />

      <div className="actions">
        <label>
          每日分钟
          <input
            className="input small"
            value={minutes}
            onChange={(e) => setMinutes(e.target.value)}
          />
        </label>
        <button className="btn primary" disabled={busy} onClick={() => apply("limit")}>
          设为限额
        </button>
        <button className="btn danger" disabled={busy} onClick={() => apply("block")}>
          直接禁用
        </button>
      </div>

      <div className="actions actions-schedule" title="只允许在此时段使用，其余时间锁定所有受限 App">
        <span className="field-label">时间窗</span>
        <input
          className="input time"
          value={start}
          onChange={(e) => setStart(e.target.value)}
          aria-label="开始时间"
        />
        <span className="field-sep">–</span>
        <input
          className="input time"
          value={end}
          onChange={(e) => setEnd(e.target.value)}
          aria-label="结束时间"
        />
        <button className="btn" onClick={enableSchedule}>
          启用
        </button>
        <button className="btn" onClick={disableSchedule}>
          关闭
        </button>
      </div>

      <h3>选择应用{selected.size > 0 ? `（已选 ${selected.size}）` : ""}</h3>
      <input
        className="input"
        placeholder="搜索应用名或 bundle id…"
        value={filter}
        onChange={(e) => setFilter(e.target.value)}
      />
      <div className="applist applist-fill">
        {shown.map((a) => (
          <label key={a.bundle_id} className="appitem">
            <input
              type="checkbox"
              checked={selected.has(a.bundle_id)}
              onChange={() => toggle(a.bundle_id)}
            />
            {icons[a.bundle_id] ? (
              <img className="app-ico" src={icons[a.bundle_id]} alt="" />
            ) : (
              <span className="app-ico placeholder" />
            )}
            <span className="appname">{a.name}</span>
            <span className="mono small appbid">{a.bundle_id}</span>
            {a.used_min != null && a.used_min > 0 && (
              <span className="usage-badge">今日 {fmtUsage(a.used_min)}</span>
            )}
          </label>
        ))}
        {shown.length === 0 && <p className="muted" style={{ padding: "8px 10px" }}>没有匹配的应用</p>}
      </div>
    </section>
  );
}
