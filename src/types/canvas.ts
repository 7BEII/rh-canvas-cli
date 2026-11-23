export type PortKind = 'image' | 'video' | 'audio' | 'text';

export type NodeType =
  | 'source'
  | 'prompt'
  | 'image'
  | 'video'
  | 'audio'
  | 'storyboard'
  | 'shotExtract'
  | 'transition'
  | 'ttsIndex'
  | 'videoEdit'
  | 'gallery'
  | 'save';

export type NodeCategory = 'input' | 'generate' | 'process' | 'output';

export type VideoGenMode = 'omni' | 't2v' | 'firstFrame' | 'firstLastFrame';

export interface LocalMediaRef {
  id: string;
  kind: 'image' | 'video' | 'audio';
  name: string;
  url: string;
  path?: string;
}

export interface CardRef {
  kind: PortKind;
  from: string;
  label?: string;
  dataUrl?: string;
  start?: number;
  volume?: number;
}

export interface CardResult {
  kind: PortKind;
  label?: string;
  text?: string;
  url?: string;
  dataUrl?: string;
  path?: string;
  name?: string;
  dims?: string;
  backendPath?: boolean;
}

export interface CanvasCard {
  id: string;
  type: NodeType;
  name: string;
  x: number;
  y: number;
  width?: number;
  height?: number;
  mediaKind?: 'image' | 'video' | 'audio';
  arkAssetId?: string;
  selected?: boolean;
  prompt?: string;
  text?: string;
  model?: string;
  aspect?: string;
  resolution?: string;
  shotCount?: number;
  duration?: number;
  videoMode?: VideoGenMode;
  generateAudio?: boolean;
  quality?: 'standard' | 'high';
  seed?: number | null;
  returnLastFrame?: boolean;
  stylePreset?: string;
  batchCount?: number;
  localMedia?: LocalMediaRef[];
  refs: CardRef[];
  results: CardResult[];
  activeResultIndex?: number;
  groupId?: string;
  groupName?: string;
  status?: 'idle' | 'queued' | 'running' | 'done' | 'error';
  error?: string;
  needsUpdate?: boolean;
  staleReason?: string;
  params?: Record<string, unknown>;
}

export interface ViewportState {
  x: number;
  y: number;
  z: number;
}

export interface CanvasSnapshot {
  format?: string;
  version?: number;
  exportedAt?: string;
  schemaVersion?: number;
  outputDir?: string;
  canvas: {
    cards: CanvasCard[];
    seq: number;
    viewport: ViewportState;
  };
}

export interface ProjectSummary {
  schema_version: number;
  id: string;
  name: string;
  created_at: string;
  updated_at: string;
  node_count: number;
  thumbnail?: string;
}

export interface ProjectRecord extends ProjectSummary {
  snapshot: CanvasSnapshot;
}

export interface NodePortSpec {
  kind: PortKind;
  label: string;
}

export interface NodeDefinition {
  type: NodeType;
  title: string;
  sub: string;
  category: NodeCategory;
  icon: string;
  color: string;
  inputs: NodePortSpec[];
  outputs: NodePortSpec[];
  defaultWidth?: number;
  defaultModel?: string;
  models?: Array<{ id: string; label: string }>;
}

export interface PresetTemplate {
  id: string;
  name: string;
  description: string;
  badge: string;
  accent: string;
  steps: string[];
  build: () => { cards: CanvasCard[]; viewport: ViewportState };
}

export interface AppSettings {
  video_provider?: 'runninghub' | 'ark';
  ark_api_key: string;
  ark_base_url: string;
  ark_video_model: string;
  ark_image_model: string;
  volc_ak: string;
  volc_sk: string;
  runninghub_api_key: string;
  runninghub_base_url: string;
  llm_api_key: string;
  llm_base_url: string;
  llm_model: string;
  comfyui_url: string;
  output_dir: string;
  allow_mock_fallback: boolean;
}

export interface TaskLogEntry {
  id: string;
  node_id: string;
  node_name: string;
  node_type: string;
  status: 'queued' | 'running' | 'done' | 'error';
  message: string;
  created_at: string;
  finished_at?: string;
  duration_ms?: number;
  provider?: string;
}
