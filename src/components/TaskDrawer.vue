<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue';
import { X, RefreshCw, Download, Play, Film, Trash2 } from 'lucide-vue-next';
import { apiClient } from '@/api/client';
import { useCanvasStore } from '@/stores/canvas';

interface OutputItem {
  name: string;
  rel_path: string;
  kind: 'video' | 'image';
  url: string;
  size: number;
  mtime: number;
  created_at: string;
}

const emit = defineEmits<{
  (e: 'close'): void;
}>();

const canvas = useCanvasStore();
const tab = ref<'outputs' | 'logs'>('outputs');
const loading = ref(false);
const outputs = ref<OutputItem[]>([]);
const previewItem = ref<OutputItem | null>(null);
const ctxMenu = ref<{ x: number; y: number; item: OutputItem } | null>(null);

// Only show real generated output videos (newest first)
const videoOutputs = computed(() => outputs.value.filter((item) => item.kind === 'video'));

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
          历史视频 ({{ videoOutputs.length }})
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

    <!-- Tab 1: Historical Output Videos Grid -->
    <div v-if="tab === 'outputs'" class="drawer-body">
      <div v-if="!videoOutputs.length" class="empty">data/outputs/videos 中暂无历史成片</div>
      <div v-else class="video-grid">
        <article
          v-for="item in videoOutputs"
          :key="item.url"
          class="out-card"
          title="左键大图预览 · 右键菜单删除"
          @click="previewItem = item"
          @contextmenu.prevent="openContextMenu($event, item)"
        >
          <div class="out-media">
            <video
              :src="item.url"
              muted
              preload="metadata"
              playsinline
              class="out-video"
            />
            <span class="play-badge">
              <Play :size="16" fill="currentColor" />
            </span>
          </div>
          <div class="out-meta">
            <div class="out-name" :title="item.name">
              <Film :size="11" class="lime" />
              <span>{{ item.name }}</span>
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
          <span class="state">{{ task.status === 'done' ? '完成' : task.status === 'error' ? '失败' : '运行中' }}</span>
        </div>
        <p>{{ task.message }}</p>
        <div class="task-meta">
          <span>{{ task.provider || task.node_type }}</span>
          <span v-if="task.duration_ms !== undefined">{{ task.duration_ms }} ms</span>
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
          <Download :size="13" /> 下载成片
        </a>
        <button type="button" class="ctx-item danger" @click="handleDeleteOutput(ctxMenu.item)">
          <Trash2 :size="13" /> 删除视频
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
              <span class="lightbox-meta">{{ formatSize(previewItem.size) }} · {{ formatTime(previewItem.created_at) }}</span>
            </div>
            <div class="lightbox-actions">
              <a :href="previewItem.url" :download="previewItem.name" class="btn">
                <Download :size="14" /> 下载成片
              </a>
              <button type="button" class="btn icon ghost" @click="previewItem = null">
                <X :size="17" />
              </button>
            </div>
          </header>
          <div class="lightbox-stage">
            <video
              :src="previewItem.url"
              controls
              autoplay
              loop
              playsinline
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
  border: 1px solid var(--line-strong);
  box-shadow: 0 24px 64px rgba(0, 0, 0, 0.65);
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

.lightbox-head {
  padding: 12px 16px;
  border-bottom: 1px solid var(--line);
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
}

.lightbox-title strong {
  font-size: 13.5px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.lightbox-meta {
  font-size: 11.5px;
  color: var(--muted);
  white-space: nowrap;
}

.lightbox-actions {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-shrink: 0;
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
}

.task-meta {
  display: flex;
  justify-content: space-between;
  font-size: 11px;
  color: var(--muted);
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
