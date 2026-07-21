import { useState } from "react";
import { GuardStatus } from "../types";
import { ExecAdmin } from "../lib/shared";

export function SitesTab({
  status,
  onChange,
  execAdmin,
}: {
  status: GuardStatus | null;
  onChange: () => void;
  execAdmin: ExecAdmin;
}) {
  const [domain, setDomain] = useState("");

  const add = async () => {
    if (!domain.trim()) return;
    const r = await execAdmin(["block-site", ...domain.trim().split(/\s+/)], {
      needsPassword: false,
    });
    if (r === null) return;
    setDomain("");
    onChange();
  };
  const remove = async (d: string) => {
    const r = await execAdmin(["unblock-site", d]);
    if (r === null) return;
    onChange();
  };

  return (
    <section>
      <div className="actions">
        <input
          className="input"
          placeholder="域名（如 youtube.com，可空格分隔多个）"
          value={domain}
          onChange={(e) => setDomain(e.target.value)}
        />
        <button className="btn" onClick={add}>
          屏蔽
        </button>
      </div>
      <ul className="list">
        {(status?.sites ?? []).map((s) => (
          <li key={s}>
            <span className="mono">{s}</span>
            <button className="btn small" onClick={() => remove(s)}>
              解除
            </button>
          </li>
        ))}
      </ul>
    </section>
  );
}
