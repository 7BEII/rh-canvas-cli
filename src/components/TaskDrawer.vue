<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue';
import { X, RefreshCw, Download, Play, Film, Image as ImageIcon, Trash2, Copy, Check, RotateCcw } from 'lucide-vue-next';
import { apiClient } from '@/api/client';
import { useCanvasStore } from '@/stores/canvas';

interface OutputMeta {
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
  duration_ms?: number;
  started_at?: string;
  created_at?: string;
}

interface OutputItem {
  name: string;
  rel_path: string;
  kind: 'video' | 'image';
  url: string;
  size: number;
  mtime: number;
  created_at: string;
  meta?: OutputMeta | null;
}

const emit = defineEmits<{
  (e: 'close'): void;
}>();

const canvas = useCanvasStore();
const tab = ref<'outputs' | 'logs'>('outputs');
const mediaFilter = ref<'all' | 'video' | 'image'>('all');
const loading = ref(false);
const outputs = ref<OutputItem[]>([]);
const previewItem = ref<OutputItem | null>(null);
const ctxMenu = ref<{ x: number; y: number; item: OutputItem } | null>(null);
const copiedTaskId = ref<string | null>(null);

async function copyText(text: string) {
  if (navigator.clipboard?.writeText) {
    try {
      await navigator.clipboard.writeText(text);
      return true;
    } catch {
      // fallback below
    }
  }
  try {
    const ta = document.createElement('textarea');
    ta.value = text;
    ta.style.position = 'fixed';
    ta.style.opacity = '0';
    document.body.appendChild(ta);
    ta.select();
    const ok = document.execCommand('copy');
    document.body.removeChild(ta);
    return ok;
  } catch {
    return false;
  }
}

async function copyTaskMessage(task: { id: string; message: string; status: string }) {
  const raw = String(task.message || '').trim();
  if (!raw) return;
  const ok = await copyText(raw);
  if (ok) {
    copiedTaskId.value = task.id;
    canvas.showToast(task.status === 'error' ? '已复制原始报错信息' : '已复制日志内容');
    window.setTimeout(() => {
      if (copiedTaskId.value === task.id) copiedTaskId.value = null;
    }, 1800);
  } else {
    canvas.showToast('复制失败，请手动选中复制');
  }
}

const videoOutputs = computed(() => outputs.value.filter((item) => item.kind === 'video'));
const imageOutputs = computed(() => outputs.value.filter((item) => item.kind === 'image'));
const filteredOutputs = computed(() => {
  if (mediaFilter.value === 'video') return videoOutputs.value;
  if (mediaFilter.value === 'image') return imageOutputs.value;
  return outputs.value;
});

function onOutputDragStart(event: DragEvent, item: OutputItem) {
  if (!event.dataTransfer) return;
  event.dataTransfer.setData('application/x-rh-history-output', JSON.stringify(item));
  event.dataTransfer.effectAllowed = 'copy';
}

function restoreToCanvasCenter(item: OutputItem) {
  ctxMenu.value = null;
  const cx = Math.round((window.innerWidth / 2 - canvas.viewport.x) / canvas.viewport.z - 120);
  const cy = Math.round((window.innerHeight / 2 - canvas.viewport.y) / canvas.viewport.z - 120);
  canvas.restoreFromHistoryOutput(item, cx, cy);
}

watch(
  () => canvas.tasks.length,
  () => {
    void refreshAll();
  },
);

async function refreshAll() {
  loading.value = true;
  try {
    const [outRes] = await Promise.all([apiClient.listOutputs(), canvas.refreshTasks()]);
    outputs.value = outRes.outputs || [];
  } catch {
    // ignore
  } finally {
    loading.value = false;
  }
}

function formatSize(bytes: number) {
  if (bytes >= 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  return `${Math.max(1, Math.round(bytes / 1024))} KB`;
}

function formatTime(iso: string) {
  try {
    return new Date(iso).toLocaleString('zh-CN', {
      month: '2-digit',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
    });
  } catch {
    return '';
  }
}

function formatLogTime(iso?: string) {
  if (!iso) return '';
  try {
    return new Date(iso).toLocaleString('zh-CN', {
      month: '2-digit',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit',
      hour12: false,
    });
  } catch {
    return '';
  }
}

function formatFullTime(iso?: string) {
  if (!iso) return '';
  try {
    return new Date(iso).toLocaleString('zh-CN', {
      year: 'numeric',
      month: '2-digit',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit',
      hour12: false,
    });
  } catch {
    return '';
  }
}

function formatDuration(ms?: number) {
  if (ms === undefined || ms === null || Number.isNaN(ms)) return '';
  if (ms < 1000) return `${Math.max(0, Math.round(ms))}ms`;
  const totalSec = ms / 1000;
  if (totalSec < 60) {
    return `${totalSec < 10 ? totalSec.toFixed(1) : Math.round(totalSec)}秒`;
  }
  const mins = Math.floor(totalSec / 60);
  const secs = Math.round(totalSec % 60);
  if (secs === 60) return `${mins + 1}分0秒`;
  return `${mins}分${secs}秒`;
}

function openContextMenu(event: MouseEvent, item: OutputItem) {
  ctxMenu.value = {
    x: Math.min(window.innerWidth - 150, event.clientX),
    y: Math.min(window.innerHeight - 130, event.clientY),
    item,
  };
}

function closeContextMenu() {
  ctxMenu.value = null;
}

async function handleDeleteOutput(item: OutputItem) {
  ctxMenu.value = null;
  try {
    await apiClient.deleteOutput(item.rel_path);
    outputs.value = outputs.value.filter((o) => o.rel_path !== item.rel_path);
    if (previewItem.value?.rel_path === item.rel_path) previewItem.value = null;
    canvas.showToast(`已删除：${item.name}`);
  } catch (err: any) {
    canvas.showToast(`删除失败：${err?.message || '未知错误'}`);
  }
}

function onKeyDown(e: KeyboardEvent) {
  if (e.key === 'Escape') {
    ctxMenu.value = null;
    if (previewItem.value) previewItem.value = null;
  }
}

onMounted(() => {
  void refreshAll();
  window.addEventListener('keydown', onKeyDown);
  window.addEventListener('click', closeContextMenu);
});

onBeforeUnmount(() => {
  window.removeEventListener('keydown', onKeyDown);
  window.removeEventListener('click', closeContextMenu);
});
</script>

<template>
  <aside class="drawer" @pointerdown.stop>
    <header class="drawer-head">
      <div class="tab-pills">
        <button type="button" class="tab-pill" :class="{ active: tab === 'outputs' }" @click="tab = 'outputs'">
          历史产物 ({{ outputs.length }})
        </button>
        <button type="button" class="tab-pill" :class="{ active: tab === 'logs' }" @click="tab = 'logs'">
          运行日志
        </button>
      </div>
      <div class="head-actions">
        <button class="btn icon ghost" type="button" title="刷新" @click="refreshAll">
          <RefreshCw :size="14" :class="{ spin: loading }" />
        </button>
        <button class="btn icon ghost" type="button" @click="emit('close')">
          <X :size="15" />
        </button>
      </div>
    </header>

    <!-- Tab 1: Historical Outputs Grid (All / Video / Image) -->
    <div v-if="tab === 'outputs'" class="drawer-body">
      <div class="filter-bar">
        <button type="button" class="filter-chip" :class="{ active: mediaFilter === 'all' }" @click="mediaFilter = 'all'">
          全部 ({{ outputs.length }})
        </button>
        <button type="button" class="filter-chip" :class="{ active: mediaFilter === 'video' }" @click="mediaFilter = 'video'">
          视频 ({{ videoOutputs.length }})
        </button>
        <button type="button" class="filter-chip" :class="{ active: mediaFilter === 'image' }" @click="mediaFilter = 'image'">
          图片 ({{ imageOutputs.length }})
        </button>
      </div>
      <div v-if="!filteredOutputs.length" class="empty">暂无对应的历史产物</div>
      <div v-else class="video-grid">
        <article
          v-for="item in filteredOutputs"
          :key="item.url"
          class="out-card"
          draggable="true"
          :title="item.meta ? '按住拖到画布回溯当时生成配置 · 左键预览 · 右键菜单' : '按住拖到画布复用素材 · 左键预览 · 右键菜单'"
          @dragstart="onOutputDragStart($event, item)"
          @click="previewItem = item"
          @contextmenu.prevent="openContextMenu($event, item)"
        >
          <div class="out-media">
            <video
              v-if="item.kind === 'video'"
              :src="item.url"
              muted
              preload="metadata"
              playsinline
              class="out-video"
            />
            <img
              v-else
              :src="item.url"
              :alt="item.name"
              loading="lazy"
              draggable="false"
              class="out-video"
            />
            <span v-if="item.kind === 'video'" class="play-badge">
              <Play :size="16" fill="currentColor" />
            </span>
            <span v-if="item.meta" class="meta-badge" title="含生成配置，拖到画布可回溯">可回溯</span>
            <span
              v-if="item.meta?.duration_ms !== undefined && item.meta?.duration_ms !== null"
              class="duration-badge"
              :title="`调用耗时：${formatDuration(item.meta.duration_ms)} (${item.meta.duration_ms} ms)`"
            >
              耗时 {{ formatDuration(item.meta.duration_ms) }}
            </span>
          </div>
          <div class="out-meta">
            <div class="out-name" :title="item.name">
              <Film v-if="item.kind === 'video'" :size="11" class="lime" />
              <ImageIcon v-else :size="11" class="sky" />
              <span>{{ item.meta?.name || item.name }}</span>
            </div>
            <div class="out-sub">
              <span>{{ formatSize(item.size) }}</span>
              <span>{{ formatTime(item.created_at) }}</span>
            </div>
          </div>
        </article>
      </div>
    </div>

    <!-- Tab 2: Task Logs -->
    <div v-else class="drawer-body">
      <div v-if="!canvas.tasks.length" class="empty">暂无运行记录，点击节点上的「运行」即可查看执行日志。</div>
      <div v-for="task in canvas.tasks" :key="task.id" class="task-row" :class="task.status">
        <div class="task-top">
          <b>{{ task.node_name }}</b>
          <div class="task-top-right">
            <button
              v-if="task.message"
              type="button"
              class="copy-log-btn"
              :class="{ copied: copiedTaskId === task.id }"
              title="复制原始信息"
              @click.stop="copyTaskMessage(task)"
            >
              <Check v-if="copiedTaskId === task.id" :size="11" />
              <Copy v-else :size="11" />
              <span>{{ copiedTaskId === task.id ? '已复制' : 'Copy' }}</span>
            </button>
            <span class="state">{{ task.status === 'done' ? '完成' : task.status === 'error' ? '失败' : '运行中' }}</span>
          </div>
        </div>
        <p>{{ task.message }}</p>
        <div class="task-meta">
          <span class="task-provider" :title="task.provider || task.node_type">{{ task.provider || task.node_type }}</span>
          <div class="task-meta-sub">
            <span v-if="task.created_at" class="task-time" :title="`调用时间：${formatFullTime(task.created_at)}`">
              调用 {{ formatLogTime(task.created_at) }}
            </span>
            <span
              v-if="task.duration_ms !== undefined"
              class="task-duration"
              :title="`${task.duration_ms} ms`"
            >
              耗时 {{ formatDuration(task.duration_ms) }}
            </span>
          </div>
        </div>
      </div>
    </div>

    <!-- Right-Click Context Menu -->
    <Teleport to="body">
      <div
        v-if="ctxMenu"
        class="ctx-menu"
        :style="{ left: ctxMenu.x + 'px', top: ctxMenu.y + 'px' }"
        @click.stop
        @pointerdown.stop
      >
        <button type="button" class="ctx-item" @click="restoreToCanvasCenter(ctxMenu.item)">
          <RotateCcw :size="13" /> {{ ctxMenu.item.meta ? '回溯配置到画布' : '添加到画布' }}
        </button>
        <button
          type="button"
          class="ctx-item"
          @click="
            previewItem = ctxMenu.item;
            ctxMenu = null;
          "
        >
          <Play :size="13" /> 大图预览
        </button>
        <a
          :href="ctxMenu.item.url"
          :download="ctxMenu.item.name"
          class="ctx-item"
          @click="ctxMenu = null"
        >
          <Download :size="13" /> 下载文件
        </a>
        <button type="button" class="ctx-item danger" @click="handleDeleteOutput(ctxMenu.item)">
          <Trash2 :size="13" /> 删除文件
        </button>
      </div>
    </Teleport>

    <!-- Fullscreen Video Lightbox Preview -->
    <Teleport to="body">
      <div v-if="previewItem" class="lightbox-backdrop" @click.self="previewItem = null" @pointerdown.stop>
        <div class="lightbox-box">
          <header class="lightbox-head">
            <div class="lightbox-title">
              <Film :size="15" class="lime" />
              <strong>{{ previewItem.name }}</strong>
              <span class="lightbox-meta">
                {{ formatSize(previewItem.size) }} · {{ formatTime(previewItem.created_at) }}
                <template v-if="previewItem.meta?.duration_ms !== undefined && previewItem.meta?.duration_ms !== null">
                  · 耗时 {{ formatDuration(previewItem.meta.duration_ms) }}
                </template>
              </span>
            </div>
            <div class="lightbox-actions">
              <a :href="previewItem.url" :download="previewItem.name" class="lightbox-dl-btn">
                <Download :size="14" /> 下载成片
              </a>
              <button type="button" class="lightbox-close-btn" title="关闭预览" @click="previewItem = null">
                <X :size="16" />
              </button>
            </div>
          </header>
          <div class="lightbox-stage">
            <video
              v-if="previewItem.kind === 'video'"
              :src="previewItem.url"
              controls
              autoplay
              loop
              playsinline
              class="lightbox-video"
            />
            <img
              v-else
              :src="previewItem.url"
              :alt="previewItem.name"
              class="lightbox-video"
            />
          </div>
        </div>
      </div>
    </Teleport>
  </aside>
</template>

<style scoped>
.drawer {
  position: fixed;
  top: 64px;
  right: 16px;
  bottom: 16px;
  z-index: 65;
  width: 368px;
  max-width: calc(100vw - 32px);
  border-radius: 16px;
  border: 1px solid var(--line-strong);
  background: rgba(24, 24, 27, 0.96);
  backdrop-filter: blur(20px);
  box-shadow: var(--shadow);
  display: flex;
  flex-direction: column;
  min-height: 0;
  overflow: hidden;
}

.drawer-head {
  padding: 10px 12px;
  border-bottom: 1px solid var(--line);
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  flex-shrink: 0;
}

.tab-pills {
  display: inline-flex;
  gap: 4px;
  padding: 2px;
  border-radius: 999px;
  background: var(--surface-group);
}

.tab-pill {
  border: 0;
  border-radius: 999px;
  padding: 5px 11px;
  background: transparent;
  color: var(--muted);
  font-size: 12px;
  font-weight: 500;
  white-space: nowrap;
}

.tab-pill.active {
  background: var(--surface-selected);
  color: #ffffff;
}

.head-actions {
  display: flex;
  gap: 4px;
}

.drawer-body {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  padding: 12px;
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.filter-bar {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 6px;
  flex-shrink: 0;
}

.filter-chip {
  border: 1px solid var(--line);
  border-radius: 9px;
  padding: 5px 6px;
  background: var(--surface-group);
  color: var(--muted);
  font-size: 11.5px;
  font-weight: 500;
  cursor: pointer;
}

.filter-chip.active {
  background: var(--surface-selected);
  border-color: var(--accent-border);
  color: var(--accent);
  font-weight: 600;
}

.meta-badge {
  position: absolute;
  top: 6px;
  left: 6px;
  padding: 1px 6px;
  border-radius: 999px;
  background: rgba(15, 15, 17, 0.8);
  border: 1px solid rgba(204, 255, 0, 0.45);
  color: var(--accent);
  font-size: 10px;
  font-weight: 600;
}

.duration-badge {
  position: absolute;
  right: 6px;
  bottom: 6px;
  padding: 1px 6px;
  border-radius: 999px;
  background: rgba(15, 15, 17, 0.82);
  border: 1px solid rgba(255, 255, 255, 0.16);
  color: rgba(255, 255, 255, 0.9);
  font-size: 10px;
  font-variant-numeric: tabular-nums;
}

.sky {
  color: #62b5ff;
  flex-shrink: 0;
}

.empty {
  padding: 32px 14px;
  text-align: center;
  color: var(--muted);
  font-size: 12px;
}

.video-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 10px;
}

.out-card {
  flex-shrink: 0;
  border-radius: 12px;
  overflow: hidden;
  background: var(--panel);
  border: 1px solid var(--line);
  cursor: pointer;
  transition: border-color 0.14s ease, transform 0.14s ease;
}

.out-card:hover {
  border-color: var(--accent);
  transform: translateY(-1px);
}

.out-media {
  position: relative;
  width: 100%;
  aspect-ratio: 9 / 14;
  background: #0b0b0d;
  overflow: hidden;
}

.out-video {
  width: 100%;
  height: 100%;
  object-fit: cover;
  display: block;
}

.play-badge {
  position: absolute;
  left: 50%;
  top: 50%;
  transform: translate(-50%, -50%);
  width: 36px;
  height: 36px;
  border-radius: 50%;
  background: rgba(0, 0, 0, 0.62);
  border: 1px solid rgba(255, 255, 255, 0.32);
  color: #ffffff;
  display: grid;
  place-items: center;
  transition: background 0.14s ease, color 0.14s ease, border-color 0.14s ease;
}

.out-card:hover .play-badge {
  background: var(--accent);
  border-color: var(--accent);
  color: #11130a;
}

.out-meta {
  padding: 7px 8px;
  display: grid;
  gap: 3px;
}

.out-name {
  display: flex;
  align-items: center;
  gap: 4px;
  font-size: 11px;
  font-weight: 500;
}

.out-name span {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.lime {
  color: var(--accent);
  flex-shrink: 0;
}

.out-sub {
  display: flex;
  align-items: center;
  justify-content: space-between;
  font-size: 10px;
  color: var(--muted);
}

/* Right-Click Context Menu */
.ctx-menu {
  position: fixed;
  z-index: 300;
  width: 142px;
  padding: 5px;
  border-radius: 12px;
  background: rgba(25, 25, 28, 0.98);
  border: 1px solid var(--line-strong);
  box-shadow: var(--shadow);
  backdrop-filter: blur(16px);
  display: grid;
  gap: 2px;
}

.ctx-item {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
  padding: 7px 9px;
  border: 0;
  border-radius: 8px;
  background: transparent;
  color: var(--text);
  font-size: 12.5px;
  text-align: left;
  text-decoration: none;
  cursor: pointer;
}

.ctx-item:hover {
  background: rgba(255, 255, 255, 0.08);
}

.ctx-item.danger {
  color: #ff8080;
}

.ctx-item.danger:hover {
  background: rgba(255, 107, 107, 0.16);
  color: #ffb8b8;
}

/* Fullscreen Lightbox */
.lightbox-backdrop {
  position: fixed;
  inset: 0;
  z-index: 260;
  background: rgba(0, 0, 0, 0.82);
  backdrop-filter: blur(10px);
  display: grid;
  place-items: center;
  padding: 24px;
}

.lightbox-box {
  width: min(860px, 94vw);
  max-height: 92vh;
  border-radius: 18px;
  background: #161618;
  color: #f5f5f7;
  border: 1px solid rgba(255, 255, 255, 0.14);
  box-shadow: 0 24px 64px rgba(0, 0, 0, 0.65);
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

.lightbox-head {
  padding: 12px 16px;
  background: rgba(255, 255, 255, 0.03);
  border-bottom: 1px solid rgba(255, 255, 255, 0.09);
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.lightbox-title {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
  color: #f5f5f7;
}

.lightbox-title .lime {
  color: #ccff00;
  flex-shrink: 0;
}

.lightbox-title strong {
  font-size: 13.5px;
  font-weight: 600;
  color: #f5f5f7;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.lightbox-meta {
  font-size: 12px;
  color: rgba(255, 255, 255, 0.62);
  white-space: nowrap;
}

.lightbox-actions {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-shrink: 0;
}

.lightbox-dl-btn {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  height: 32px;
  padding: 0 13px;
  border-radius: 999px;
  background: rgba(255, 255, 255, 0.1);
  border: 1px solid rgba(255, 255, 255, 0.16);
  color: #f5f5f7;
  font-size: 12.5px;
  font-weight: 500;
  text-decoration: none;
  white-space: nowrap;
  transition: background 0.14s ease, border-color 0.14s ease, color 0.14s ease;
}

.lightbox-dl-btn:hover {
  background: rgba(204, 255, 0, 0.14);
  border-color: rgba(204, 255, 0, 0.55);
  color: #ccff00;
}

.lightbox-close-btn {
  width: 32px;
  height: 32px;
  border-radius: 9px;
  border: 1px solid rgba(255, 255, 255, 0.14);
  background: rgba(255, 255, 255, 0.08);
  color: rgba(255, 255, 255, 0.86);
  display: grid;
  place-items: center;
  cursor: pointer;
  transition: background 0.14s ease, color 0.14s ease;
}

.lightbox-close-btn:hover {
  background: rgba(255, 255, 255, 0.18);
  color: #ffffff;
}

.lightbox-stage {
  flex: 1;
  min-height: 0;
  background: #0a0a0c;
  display: grid;
  place-items: center;
  padding: 16px;
}

.lightbox-video {
  max-width: 100%;
  max-height: 76vh;
  border-radius: 10px;
  background: #000000;
}

.task-row {
  flex-shrink: 0;
  padding: 10px 11px;
  border-radius: 10px;
  background: var(--panel-2);
  border: 1px solid var(--line);
  display: grid;
  gap: 4px;
}

.task-row.done {
  border-color: rgba(204, 255, 0, 0.3);
}

.task-row.error {
  border-color: rgba(255, 107, 107, 0.45);
}

.task-top {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 8px;
}

.task-top-right {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  flex-shrink: 0;
}

.copy-log-btn {
  display: inline-flex;
  align-items: center;
  gap: 3px;
  padding: 2px 7px;
  border-radius: 6px;
  border: 1px solid rgba(255, 255, 255, 0.14);
  background: rgba(255, 255, 255, 0.06);
  color: rgba(255, 255, 255, 0.78);
  font-size: 11px;
  line-height: 1.3;
  cursor: pointer;
  transition: background 0.14s ease, border-color 0.14s ease, color 0.14s ease;
}

.copy-log-btn:hover {
  background: rgba(255, 255, 255, 0.14);
  border-color: rgba(255, 255, 255, 0.28);
  color: #ffffff;
}

.copy-log-btn.copied {
  border-color: rgba(204, 255, 0, 0.55);
  background: rgba(204, 255, 0, 0.14);
  color: var(--accent);
}

.state {
  font-size: 11px;
  color: var(--accent);
}

.task-row.error .state {
  color: var(--danger);
}

.task-row p {
  margin: 0;
  font-size: 12px;
  color: var(--muted);
  word-break: break-word;
  user-select: text;
}

.task-meta {
  display: flex;
  flex-direction: column;
  gap: 3px;
  font-size: 11px;
  color: var(--muted);
  margin-top: 2px;
}

.task-provider {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.task-meta-sub {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 8px;
  font-variant-numeric: tabular-nums;
}

.task-time {
  color: rgba(255, 255, 255, 0.56);
}

.task-duration {
  margin-left: auto;
}

.spin {
  animation: spin 1s linear infinite;
}

@keyframes spin {
  to {
    transform: rotate(360deg);
  }
}
</style>
