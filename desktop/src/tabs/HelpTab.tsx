import { openPath } from "@tauri-apps/plugin-opener";

const FEATURES: { tab: string; accent: string; desc: string }[] = [
  { tab: "应用时长", accent: "var(--c-apps)", desc: "勾选应用设每日上限或直接禁用，并设定全天使用时段" },
  { tab: "概览", accent: "var(--c-status)", desc: "查看防护状态与今日已限制的应用" },
  { tab: "网站", accent: "var(--c-sites)", desc: "按域名屏蔽网站，hosts + 防火墙双层生效" },
  { tab: "解锁 / 密码", accent: "var(--c-unlock)", desc: "临时放行、修改家长密码、授予完全磁盘访问" },
];

const FAQS: { q: string; a: string }[] = [
  { q: "用量显示不准？", a: "到「解锁 / 密码」授予完全磁盘访问，即可读取系统精确用量。" },
  { q: "到点会强制关闭应用吗？", a: "不会。只会温和锁定、隐藏到后台，不丢数据。" },
  { q: "哪些操作要家长密码？", a: "放松限制才需要——解锁、改限额、卸载。加限制无需密码。" },
];

export function HelpTab() {
  const openLogs = async () => {
    try {
      await openPath("/Library/Application Support/GrowGuard/data");
    } catch (e) {
      console.error("打开日志目录失败", e);
    }
  };

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

      <div className="help-diag">
        <span className="help-diag-text">遇到异常？把日志发给我们能更快定位。</span>
        <button className="btn" onClick={openLogs}>
          打开日志目录
        </button>
      </div>
    </section>
  );
}
