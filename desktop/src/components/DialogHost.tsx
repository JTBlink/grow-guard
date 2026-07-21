import { useEffect, useRef, useState } from "react";
import { DialogState } from "../lib/shared";

export function DialogHost({ state, close }: { state: DialogState | null; close: () => void }) {
  const [value, setValue] = useState("");
  const [confirmValue, setConfirmValue] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const firstInput = useRef<HTMLInputElement>(null);

  useEffect(() => {
    setValue("");
    setConfirmValue("");
    setErr(null);
    if (state?.kind === "prompt") {
      const t = setTimeout(() => firstInput.current?.focus(), 0);
      return () => clearTimeout(t);
    }
  }, [state]);

  if (!state) return null;

  if (state.kind === "alert") {
    return (
      <div className="modal-scrim" onClick={() => (state.resolve(), close())}>
        <div className="dialog-card" onClick={(e) => e.stopPropagation()}>
          <p className="dialog-msg">{state.message}</p>
          <button
            className="btn primary dialog-ok"
            autoFocus
            onClick={() => (state.resolve(), close())}
          >
            好
          </button>
        </div>
      </div>
    );
  }

  const { opts } = state;
  const cancel = () => (state.resolve(null), close());
  const submit = () => {
    if (opts.confirmMessage && value !== confirmValue) {
      setErr("两次输入不一致");
      return;
    }
    state.resolve(value);
    close();
  };

  return (
    <div className="modal-scrim" onClick={cancel}>
      <div className="dialog-card" onClick={(e) => e.stopPropagation()}>
        <p className="dialog-msg">{opts.message}</p>
        <input
          ref={firstInput}
          className="input"
          type={opts.password ? "password" : "text"}
          value={value}
          onChange={(e) => setValue(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !opts.confirmMessage) submit();
            if (e.key === "Escape") cancel();
          }}
        />
        {opts.confirmMessage && (
          <input
            className="input"
            type={opts.password ? "password" : "text"}
            placeholder={opts.confirmMessage}
            value={confirmValue}
            onChange={(e) => setConfirmValue(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") submit();
              if (e.key === "Escape") cancel();
            }}
          />
        )}
        {err && <p className="dialog-err">{err}</p>}
        <div className="dialog-actions">
          <button className="btn" onClick={cancel}>
            取消
          </button>
          <button className="btn primary" onClick={submit}>
            确定
          </button>
        </div>
      </div>
    </div>
  );
}
