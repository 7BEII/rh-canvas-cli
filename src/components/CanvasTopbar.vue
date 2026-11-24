<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue';
import {
  LayoutGrid,
  List,
  Save,
  Download,
  Settings,
  Clock,
  Square,
  Loader2,
  Sparkles,
  ChevronLeft,
} from 'lucide-vue-next';
import { apiClient } from '@/api/client';
import { useCanvasStore } from '@/stores/canvas';
import { useProjectsStore } from '@/stores/projects';

defineProps<{
  viewMode: 'canvas' | 'list';
}>();

const emit = defineEmits<{
  (e: 'update:viewMode', mode: 'canvas' | 'list'): void;
  (e: 'open-settings'): void;
  (e: 'toggle-tasks'): void;
}>();

const canvas = useCanvasStore();
const projects = useProjectsStore();

const editingName = ref(false);
const draftName = ref('');
const elapsedSec = ref(0);
let timer: ReturnType<typeof setInterval> | null = null;

const runningCard = computed(() => canvas.cards.find((c) => c.status === 'running') || null);
const isAnyRunning = computed(() => canvas.running || Boolean(runningCard.value));

watch(
  isAnyRunning,
  (active) => {
    if (active) {
      elapsedSec.value = 0;
      if (timer) clearInterval(timer);
      timer = setInterval(() => {
        elapsedSec.value += 1;
      }, 1000);
    } else if (timer) {
      clearInterval(timer);
      timer = null;
    }
  },
  { immediate: true },
);

onBeforeUnmount(() => {
  if (timer) {
    clearInterval(timer);
    timer = null;
  }
});

async function handleStopRunning() {
  const target = runningCard.value;
  if (target) {
    await canvas.cancelNode(target.id);
  }
}

function startEditName() {
  draftName.value = projects.activeProjectName;
  editingName.value = true;
}

async function commitName() {
  editingName.value = false;
  const trimmed = draftName.value.trim();
  if (!trimmed || trimmed === projects.activeProjectName || !projects.activeProjectId) return;
  try {
    await projects.renameProject(projects.activeProjectId, trimmed);
    canvas.showToast('已更新画布名称');
  } catch (err: any) {
    canvas.showToast(err?.message || '重命名失败');
  }
}

async function handleBackHome() {
  await canvas.persistNow(true);
  projects.closeEditor();
  await projects.refreshList();
}

async function handleExportZip() {
  if (!projects.activeProjectId) return;
  try {
    await canvas.persistNow(true);
    const url = apiClient.exportProjectZipUrl(projects.activeProjectId);
    const a = document.createElement('a');
    a.href = url;
    a.download = `${projects.activeProjectName || 'canvas'}.zip`;
    document.body.appendChild(a);
    a.click();
    a.remove();
    canvas.showToast('正在打包下载画布 ZIP（含全部本地素材）…');
  } catch (err: any) {
    canvas.showToast(`导出失败：${err?.message || '未知错误'}`);
  }
}
</script>

<template>
  <header class="rhtv-topbar">
    <div class="bar-left">
      <button class="logo-btn" type="button" title="返回全部画布" @click="handleBackHome">
        <ChevronLeft :size="15" />
        <span class="logo-mark" aria-hidden="true">
          <svg viewBox="0 0 24 24" width="16" height="16" fill="none">
            <rect x="2.5" y="4" width="6.5" height="6.5" rx="2" stroke="#ffffff" stroke-opacity="0.9" stroke-width="1.7" />
            <rect x="2.5" y="13.5" width="6.5" height="6.5" rx="2" stroke="#ffffff" stroke-opacity="0.5" stroke-width="1.7" />
            <rect x="14.5" y="8.2" width="7" height="7.6" rx="2" fill="#ccff00" />
            <path d="M9 7.2C11.8 7.2 11.6 12 14.5 12" stroke="#ccff00" stroke-width="1.7" stroke-linecap="round" />
            <path d="M9 16.8C11.8 16.8 11.6 12 14.5 12" stroke="#ffffff" stroke-opacity="0.45" stroke-width="1.5" stroke-linecap="round" />
          </svg>
        </span>
      </button>

      <div class="title-wrap">
        <input
          v-if="editingName"
          v-model="draftName"
          class="field title-input"
          autofocus
          @blur="commitName"
          @keydown.enter="commitName"
        />
        <button v-else class="title-btn" type="button" title="点击重命名" @click="startEditName">
          {{ projects.activeProjectName }}
        </button>
      </div>

      <div class="mode-switch">
        <button
          type="button"
          class="mode-pill"
          :class="{ active: viewMode === 'canvas' }"
          @click="emit('update:viewMode', 'canvas')"
        >
          <LayoutGrid :size="13" />
          画布
        </button>
        <button
          type="button"
          class="mode-pill"
          :class="{ active: viewMode === 'list' }"
          @click="emit('update:viewMode', 'list')"
        >
          <List :size="13" />
          列表
        </button>
      </div>

      <button class="pill-icon-btn" type="button" title="保存 (Ctrl+S)" @click="canvas.persistNow(false)">
        <Save :size="15" />
      </button>
      <button class="pill-icon-btn" type="button" title="打包导出画布 ZIP（含素材）" @click="handleExportZip">
        <Download :size="15" />
      </button>
    </div>

    <div class="bar-right">
      <span v-if="projects.saving" class="status-text">保存中…</span>

      <button class="pill-icon-btn" type="button" title="历史记录与成片" @click="emit('toggle-tasks')">
        <Clock :size="15" />
      </button>

      <button class="pill-icon-btn" type="button" title="API 与全局设置" @click="emit('open-settings')">
        <Settings :size="15" />
      </button>

      <button
        v-if="isAnyRunning"
        class="stop-pill"
        type="button"
        title="立即中止当前生成任务"
        @click="handleStopRunning"
      >
        <Square :size="11" fill="currentColor" />
        <span>停止</span>
      </button>

      <button
        class="run-pill"
        type="button"
        :disabled="isAnyRunning"
        @click="canvas.runNodesInOrder()"
      >
        <Loader2 v-if="isAnyRunning" :size="13" class="spin" />
        <Sparkles v-else :size="13" class="spark" />
        <span>
          {{
            isAnyRunning
              ? `生成中 · ${runningCard?.name || '画布'} ${elapsedSec}s`
              : '运行画布'
          }}
        </span>
      </button>
    </div>
  </header>
</template>

<style scoped>
.rhtv-topbar {
  position: fixed;
  top: 0;
  left: 0;
  right: 0;
  height: 58px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 20px;
  z-index: 40;
  pointer-events: none;
}

.bar-left,
.bar-right {
  display: flex;
  align-items: center;
  gap: 8px;
  pointer-events: auto;
}

.logo-btn {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  height: 36px;
  padding: 0 10px 0 6px;
  border-radius: 999px;
  border: 1px solid var(--line);
  background: var(--toolbar-surface);
  color: var(--text);
  backdrop-filter: blur(20px);
  box-shadow: var(--toolbar-shadow);
}

.logo-btn:hover {
  border-color: var(--line-strong);
  background: var(--surface-selected);
}

.logo-mark {
  display: grid;
  place-items: center;
  width: 20px;
  height: 20px;
}

.title-wrap {
  display: flex;
  align-items: center;
}

.title-btn {
  border: 0;
  background: transparent;
  color: var(--text);
  font-size: 14px;
  font-weight: 600;
  padding: 6px 10px;
  border-radius: 10px;
  max-width: 280px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.title-btn:hover {
  background: rgba(255, 255, 255, 0.08);
}

.title-input {
  width: 230px;
  min-height: 32px;
  font-size: 13.5px;
  font-weight: 600;
}

.mode-switch {
  display: inline-flex;
  align-items: center;
  padding: 3px;
  border-radius: 999px;
  background: var(--toolbar-surface);
  border: 1px solid var(--line);
  backdrop-filter: blur(20px);
  box-shadow: var(--toolbar-shadow);
}

.mode-pill {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  height: 28px;
  padding: 0 12px;
  border: 0;
  border-radius: 999px;
  background: transparent;
  color: var(--muted);
  font-size: 12px;
  font-weight: 500;
  white-space: nowrap;
  flex-shrink: 0;
  transition: background 0.15s ease, color 0.15s ease;
}

.mode-pill.active {
  background: var(--surface-selected);
  color: var(--text);
}

.pill-icon-btn {
  width: 34px;
  height: 34px;
  border-radius: 50%;
  border: 1px solid var(--line);
  background: var(--toolbar-surface);
  color: var(--text-secondary);
  display: grid;
  place-items: center;
  backdrop-filter: blur(20px);
  box-shadow: var(--toolbar-shadow);
  transition: color 0.14s ease, border-color 0.14s ease, background 0.14s ease;
}

.pill-icon-btn:hover {
  color: var(--text);
  border-color: var(--line-strong);
  background: var(--surface-selected);
}

.status-text {
  font-size: 12px;
  color: var(--muted);
  margin-right: 4px;
}

.run-pill {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  height: 34px;
  padding: 0 14px;
  border-radius: 999px;
  border: 1px solid var(--line-strong);
  background: var(--toolbar-surface);
  color: var(--text);
  font-size: 12.5px;
  font-weight: 600;
  white-space: nowrap;
  flex-shrink: 0;
  backdrop-filter: blur(20px);
  box-shadow: var(--toolbar-shadow);
}

.run-pill .spark {
  color: var(--accent);
}

.stop-pill {
  height: 34px;
  padding: 0 12px;
  border-radius: 999px;
  border: 1px solid rgba(255, 107, 107, 0.45);
  background: rgba(255, 107, 107, 0.14);
  color: #ffb4b4;
  font-size: 12.5px;
  font-weight: 600;
  display: inline-flex;
  align-items: center;
  gap: 5px;
  cursor: pointer;
  backdrop-filter: blur(12px);
}

.stop-pill:hover {
  background: rgba(255, 107, 107, 0.25);
  color: #ffffff;
}

.spin {
  animation: topbar-spin 1s linear infinite;
}

@keyframes topbar-spin {
  to {
    transform: rotate(360deg);
  }
}

.run-pill:hover:not(:disabled) {
  border-color: var(--accent-border);
  background: var(--surface-selected);
}

.run-pill:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}
</style>
