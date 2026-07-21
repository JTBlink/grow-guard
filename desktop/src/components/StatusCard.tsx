import { GuardStatus } from "../types";

export function fmtScreenTotal(min: number): string {
  const m = Math.round(min);
  if (m < 60) return `${m} 分钟`;
  const h = Math.floor(m / 60);
  const rest = m % 60;
  return rest === 0 ? `${h} 小时` : `${h} 小时 ${rest} 分钟`;
}

export function StatusCard({
  status,
  enabling,
  totalScreenMin,
  onEnable,
  onRelock,
}: {
  status: GuardStatus | null;
  enabling: boolean;
  totalScreenMin: number | null;
  onEnable: () => void;
  onRelock: () => void;
}) {
  if (!status) {
    return <div className="statuscard sc-idle"><div className="sc-body"><div className="sc-title">读取状态…</div></div></div>;
  }

  if (!status.daemon_running) {
    return (
      <div className="statuscard sc-off">
        <div className="sc-body">
          <div className="sc-title">防护未启用</div>
          <div className="sc-sub">已设的限制现在不会执行。启用后台守护即可开始保护。</div>
        </div>
        <button className="btn primary sc-action" disabled={enabling} onClick={onEnable}>
          {enabling ? "启用中…" : "启用防护"}
        </button>
      </div>
    );
  }

  if (status.grace_active) {
    return (
      <div className="statuscard sc-grace">
        <div className="sc-body">
          <div className="sc-title">限制已暂停</div>
          <div className="sc-sub">临时解锁中，约 {status.grace_left_min} 分钟后自动恢复。</div>
        </div>
        <button className="btn danger sc-action" onClick={onRelock}>
          立即恢复
        </button>
      </div>
    );
  }

  const sched = status.schedule;
  const windowNote = sched.enabled
    ? sched.in_window
      ? `当前允许使用（${sched.allow_start}–${sched.allow_end}）`
      : `当前时段外，受限应用已锁定（${sched.allow_start}–${sched.allow_end}）`
    : "全天守护中";

  return (
    <div className="statuscard sc-on">
      <div className="sc-dot" aria-hidden />
      <div className="sc-body">
        <div className="sc-title">
          今日屏幕总时长{" "}
          <span className="sc-figure">
            {totalScreenMin == null ? "—" : fmtScreenTotal(totalScreenMin)}
          </span>
        </div>
        <div className="sc-sub">防护运行中 · {windowNote}</div>
      </div>
    </div>
  );
}
