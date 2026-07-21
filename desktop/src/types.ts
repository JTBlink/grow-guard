// GrowGuard 状态类型 —— 对应 backend/cli.py `status --json` 的输出结构。
export interface AppRule {
  bundle_id: string;
  daily_limit_min: number | null;
  blocked: boolean;
  used_min: number;
  locked: boolean;
}

export interface Schedule {
  enabled: boolean;
  allow_start: string | null;
  allow_end: string | null;
  in_window: boolean;
}

export interface GuardStatus {
  initialized: boolean;
  daemon_running: boolean;
  usage_source: "knowledgeC" | "poll";
  grace_active: boolean;
  grace_left_min: number;
  schedule: Schedule;
  apps: AppRule[];
  sites: string[];
  error?: string;
}

export interface InstalledApp {
  name: string;
  bundle_id: string;
  path: string;
  used_min?: number;
}

export interface AdminResult {
  ok: boolean;
  output: string;
  cancelled: boolean;
}
