import { useState } from "react";
import { AlertFn, ExecAdmin, PromptOpts, isBadPassword, runAdmin } from "../lib/shared";

export function UnlockTab({
  onChange,
  alert,
  prompt,
  execAdmin,
}: {
  onChange: () => void;
  alert: AlertFn;
  prompt: (opts: PromptOpts) => Promise<string | null>;
  execAdmin: ExecAdmin;
}) {
  const [minutes, setMinutes] = useState("15");

  const unlock = async () => {
    if (!/^\d+$/.test(minutes)) {
      await alert("请输入数字");
      return;
    }
    const r = await execAdmin(["unlock", minutes]);
    if (r === null) return;
    onChange();
  };
  const relock = async () => {
    const r = await execAdmin(["relock"], { needsPassword: false });
    if (r === null) return;
    onChange();
  };
  const grantFda = async () => {
    await runAdmin(["grant-fda"]);
  };

  const changePassword = async () => {
    const oldPw = await prompt({ message: "请输入当前家长密码", password: true });
    if (oldPw === null) return;
    const newPw = await prompt({
      message: "设置新的家长密码（至少 4 位）",
      password: true,
      confirmMessage: "再次输入新密码",
    });
    if (newPw === null) return;
    if (newPw.length < 4) {
      await alert("新密码太短（至少 4 位）");
      return;
    }
    const r = await runAdmin(["passwd"], oldPw, newPw);
    if (r.cancelled) return;
    if (!r.ok) {
      await alert(isBadPassword(r.output) ? "当前密码错误" : `修改失败：${r.output}`);
      return;
    }
    await alert("家长密码已更新");
  };

  return (
    <section>
      <h3>临时解锁</h3>
      <div className="actions">
        <label>
          分钟 <input className="input small" value={minutes} onChange={(e) => setMinutes(e.target.value)} />
        </label>
        <button className="btn" onClick={unlock}>
          临时解锁
        </button>
        <button className="btn danger" onClick={relock}>
          立即恢复限制
        </button>
      </div>

      <h3>家长密码</h3>
      <div className="actions">
        <button className="btn" onClick={changePassword}>
          修改家长密码
        </button>
      </div>

      <h3>系统精确用量</h3>
      <button className="btn" onClick={grantFda}>
        引导授予完全磁盘访问
      </button>
    </section>
  );
}
