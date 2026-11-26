import type {
  AppSettings,
  CanvasCard,
  CanvasSnapshot,
  CardResult,
  ProjectRecord,
  ProjectStage,
  ProjectSummary,
  TaskLogEntry,
} from '@/types/canvas';

const API_BASE = '/api';

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const headers = new Headers(options.headers || {});
  if (!(options.body instanceof FormData) && !headers.has('Content-Type')) {
    headers.set('Content-Type', 'application/json');
  }

  let res: Response;
  try {
    res = await fetch(`${API_BASE}${path}`, {
      cache: 'no-store',
      ...options,
      headers,
    });
  } catch (err: any) {
    if (err?.name === 'AbortError') throw err;
    throw new Error('无法连接本地后端服务（127.0.0.1:5188），或等待超时连接中断，请确认 Python 后端正在运行后重试');
  }

  let data: any;
  try {
    data = await res.json();
  } catch {
    throw new Error(`后端响应异常 (HTTP ${res.status})，请确认 Python 服务已启动`);
  }

  if (!res.ok || data?.success === false) {
    throw new Error(data?.error || data?.message || `HTTP ${res.status}`);
  }
  return data as T;
}

export const apiClient = {
  health() {
    return request<{
      success: boolean;
      service: string;
      version: string;
      boot_id?: string;
      output_dir: string;
      has_ark_key: boolean;
      has_runninghub_key: boolean;
      has_tensor_key: boolean;
      has_llm_key: boolean;
    }>('/health');
  },

  restartServer() {
    return request<{ success: boolean; restarting: boolean; boot_id?: string }>('/server/restart', {
      method: 'POST',
    });
  },

  async restartServerAndWait(timeoutMs = 20000): Promise<boolean> {
    let oldBootId = '';
    try {
      const res = await this.restartServer();
      oldBootId = res.boot_id || '';
    } catch {
      // Server may close the socket right as it exits; continue polling
    }
    const deadline = Date.now() + timeoutMs;
    await new Promise((r) => window.setTimeout(r, 900));
    while (Date.now() < deadline) {
      try {
        const h = await this.health();
        if (h.success && (!oldBootId || !h.boot_id || h.boot_id !== oldBootId)) {
          return true;
        }
      } catch {
        // Still restarting
      }
      await new Promise((r) => window.setTimeout(r, 600));
    }
    return false;
  },

  testArkConnection(payload?: { ark_api_key?: string; ark_base_url?: string }) {
    return request<{ success: boolean; message: string; status_code?: number }>('/ark/test', {
      method: 'POST',
      body: JSON.stringify(payload || {}),
    });
  },

  testRunningHubConnection(payload?: { runninghub_api_key?: string; runninghub_base_url?: string }) {
    return request<{ success: boolean; message: string; status_code?: number }>('/runninghub/test', {
      method: 'POST',
      body: JSON.stringify(payload || {}),
    });
  },

  testTensorConnection(payload?: { tensor_api_key?: string; tensor_base_url?: string }) {
    return request<{ success: boolean; message: string; status_code?: number }>('/tensor/test', {
      method: 'POST',
      body: JSON.stringify(payload || {}),
    });
  },

  listProjects(stage?: ProjectStage) {
    const qs = stage ? `?stage=${encodeURIComponent(stage)}` : '';
    return request<{
      success: boolean;
      projects: ProjectSummary[];
      dirs?: { online: string; test: string };
    }>(`/projects${qs}`);
  },

  getProject(id: string) {
    return request<{ success: boolean; project: ProjectRecord }>(`/projects/${encodeURIComponent(id)}`);
  },

  createProject(payload: { name: string; snapshot?: CanvasSnapshot; stage?: ProjectStage }) {
    return request<{ success: boolean; project: ProjectRecord }>('/projects', {
      method: 'POST',
      body: JSON.stringify(payload),
    });
  },

  updateProject(id: string, payload: { name?: string; snapshot?: CanvasSnapshot; cover?: string }) {
    return request<{ success: boolean; project: ProjectRecord }>(`/projects/${encodeURIComponent(id)}`, {
      method: 'PUT',
      body: JSON.stringify(payload),
    });
  },

  setProjectStage(id: string, stage: ProjectStage) {
    return request<{ success: boolean; project: ProjectRecord }>(
      `/projects/${encodeURIComponent(id)}/stage`,
      {
        method: 'POST',
        body: JSON.stringify({ stage }),
      },
    );
  },

  duplicateProject(id: string) {
    return request<{ success: boolean; project: ProjectRecord }>(
      `/projects/${encodeURIComponent(id)}/duplicate`,
      { method: 'POST' },
    );
  },

  deleteProject(id: string) {
    return request<{ success: boolean }>(`/projects/${encodeURIComponent(id)}`, {
      method: 'DELETE',
    });
  },

  exportProjectZipUrl(id: string) {
    return `${API_BASE}/projects/${encodeURIComponent(id)}/export-zip`;
  },

  async exportSelectedProjectsZip(projectIds: string[]): Promise<{ blob: Blob; filename: string }> {
    const res = await fetch(`${API_BASE}/projects/export-zip`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ project_ids: projectIds }),
    });
    if (!res.ok) {
      let msg = `导出失败 (HTTP ${res.status})`;
      try {
        const data = await res.json();
        if (data?.error) msg = data.error;
      } catch {
        // ignore
      }
      throw new Error(msg);
    }
    const disp = res.headers.get('Content-Disposition') || '';
    const utfMatch = disp.match(/filename\*=UTF-8''([^;]+)/i);
    const plainMatch = disp.match(/filename="?([^";]+)"?/i);
    const filename = utfMatch
      ? decodeURIComponent(utfMatch[1])
      : plainMatch
        ? plainMatch[1]
        : `rh-canvas-batch-${projectIds.length}.zip`;
    return { blob: await res.blob(), filename };
  },

  importProjectZip(file: File) {
    const form = new FormData();
    form.append('file', file);
    return request<{
      success: boolean;
      mode?: 'project' | 'batch' | 'workspace';
      project?: ProjectRecord;
      projects?: ProjectRecord[];
      imported_projects?: number;
      updated_projects?: number;
      imported_assets: number;
      restored_settings?: boolean;
    }>('/projects/import-zip', {
      method: 'POST',
      body: form,
    });
  },

  exportWorkspaceZipUrl(includeSecrets = true) {
    return `${API_BASE}/workspace/export-zip?include_secrets=${includeSecrets ? '1' : '0'}`;
  },

  importWorkspaceZip(file: File) {
    const form = new FormData();
    form.append('file', file);
    return request<{
      success: boolean;
      mode: 'project' | 'workspace';
      project?: ProjectRecord;
      projects?: ProjectRecord[];
      imported_projects?: number;
      updated_projects?: number;
      imported_assets: number;
      restored_settings?: boolean;
    }>('/workspace/import-zip', {
      method: 'POST',
      body: form,
    });
  },

  uploadAsset(file: File) {
    const form = new FormData();
    form.append('file', file);
    return request<{
      success: boolean;
      asset: {
        kind: 'image' | 'video' | 'audio';
        name: string;
        url: string;
        path: string;
      };
    }>('/assets/input', {
      method: 'POST',
      body: form,
    });
  },

  getConfig() {
    return request<{ success: boolean; config: AppSettings }>('/config');
  },

  saveConfig(config: Partial<AppSettings>) {
    return request<{ success: boolean; config: AppSettings }>('/config', {
      method: 'POST',
      body: JSON.stringify(config),
    });
  },

  runNode(
    payload: { card: CanvasCard; upstream: Array<{ ref: CanvasCard['refs'][number]; card: CanvasCard }> },
    signal?: AbortSignal,
  ) {
    return request<{
      success: boolean;
      results: CardResult[];
      provider: string;
      message: string;
      task: TaskLogEntry;
    }>('/nodes/run', {
      method: 'POST',
      body: JSON.stringify(payload),
      signal,
    });
  },

  cancelNode(nodeId: string) {
    return request<{ success: boolean; cancelled: boolean; message: string }>('/nodes/cancel', {
      method: 'POST',
      body: JSON.stringify({ node_id: nodeId }),
    });
  },

  listTasks(limit = 50) {
    return request<{ success: boolean; tasks: TaskLogEntry[] }>(`/tasks?limit=${limit}`);
  },

  listOutputs() {
    return request<{
      success: boolean;
      outputs: Array<{
        name: string;
        rel_path: string;
        kind: 'video' | 'image';
        url: string;
        size: number;
        mtime: number;
        created_at: string;
        meta?: {
          node_type?: string;
          name?: string;
          model?: string;
          prompt?: string;
          aspect?: string;
          resolution?: string;
          duration?: number;
          videoMode?: string;
          stylePreset?: string;
          generateAudio?: boolean;
          provider?: string;
          created_at?: string;
        } | null;
      }>;
    }>('/outputs');
  },

  deleteOutput(relPath: string) {
    return request<{ success: boolean }>(`/outputs/${relPath.split('/').map(encodeURIComponent).join('/')}`, {
      method: 'DELETE',
    });
  },
};
