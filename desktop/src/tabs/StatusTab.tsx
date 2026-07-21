import { GuardStatus } from "../types";
import { ExecAdmin, Row } from "../lib/shared";

export function StatusTab({
  status,
  onChange,
  execAdmin,
}: {
  status: GuardStatus | null;
  onChange: () => void;
  execAdmin: ExecAdmin;
}) {
  if (!status) return <p>加载中…</p>;

  const removeLimit = async (bid: string) => {
    const r = await execAdmin(["unlimit", bid]);
    if (r === null) return;
    onChange();
  };

  return (
    <section>
      <div className="rows">
        <Row k="守护进程" v={status.daemon_running ? "运行中" : "未运行"} />
        <Row k="用量来源" v={status.usage_source === "knowledgeC" ? "系统精确" : "轮询估算"} />
        {status.grace_active && <Row k="临时解锁" v={`剩余约 ${status.grace_left_min} 分钟`} />}
        <Row
          k="时间窗"
          v={
            status.schedule.enabled
              ? `${status.schedule.allow_start} ~ ${status.schedule.allow_end}（${status.schedule.in_window ? "允许" : "锁定"}）`
              : "未启用"
          }
        />
      </div>

      <h3>已限制应用（{status.apps.length}）</h3>
      {status.apps.length === 0 && (
        <p className="muted">还没有限制任何应用。到「应用时长」页勾选应用即可开始。</p>
      )}
      <ul className="list">
        {status.apps.map((a) => (
          <li key={a.bundle_id}>
            <span className={a.locked ? "lock locked" : "lock"}>{a.locked ? "🔒" : "✓"}</span>
            <span className="mono">{a.bundle_id}</span>
            <span className="desc">
              {a.blocked
                ? "已禁用"
                : a.daily_limit_min != null
                  ? `${a.used_min.toFixed(0)} / ${a.daily_limit_min} 分钟`
                  : "仅追踪"}
            </span>
            <button className="btn small" onClick={() => removeLimit(a.bundle_id)}>
              解除
            </button>
          </li>
        ))}
      </ul>
    </section>
  );
}
