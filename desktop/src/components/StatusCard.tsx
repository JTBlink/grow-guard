import { GuardStatus } from "../types";
import { fmtUsage } from "../lib/shared";

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
  const totalUsage = totalScreenMin == null ? "" : fmtUsage(totalScreenMin);

  return (
    <div className="statuscard sc-on">
      <div className="sc-dot" aria-hidden />
      <div className="sc-body">
        <div className="sc-title">
          屏幕总时长{" "}
          <span className="sc-figure">
            {totalUsage || "—"}
          </span>
        </div>
        <div className="sc-sub">防护运行中 · {windowNote}</div>
      </div>
    </div>
  );
}
