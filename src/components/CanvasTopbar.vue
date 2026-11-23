<script setup lang="ts">
import { ref } from 'vue';
import {
  LayoutGrid,
  List,
  Save,
  Download,
  Settings,
  Clock,
  Play,
  Sparkles,
  ChevronLeft,
} from 'lucide-vue-next';
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

function handleExportJson() {
  const payload = {
    schema_version: 1,
    id: projects.activeProjectId,
    name: projects.activeProjectName,
    snapshot: canvas.buildSnapshot(),
  };
  const blob = new Blob([JSON.stringify(payload, null, 2)], { type: 'application/json' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = `${projects.activeProjectName || 'canvas'}.json`;
  a.click();
  URL.revokeObjectURL(url);
  canvas.showToast('已导出画布 JSON');
}
</script>

<template>
  <header class="rhtv-topbar">
    <div class="bar-left">
      <button class="logo-btn" type="button" title="返回全部画布" @click="handleBackHome">
        <ChevronLeft :size="16" />
        <span class="logo-mark">◇</span>
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
      <button class="pill-icon-btn" type="button" title="导出 JSON" @click="handleExportJson">
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
        class="run-pill"
        type="button"
        :disabled="canvas.running"
        @click="canvas.runNodesInOrder()"
      >
        <Sparkles v-if="!canvas.running" :size="13" class="spark" />
        <Play v-else :size="13" />
        <span>{{ canvas.running ? '生成中…' : '运行画布' }}</span>
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
  font-size: 16px;
  font-weight: 800;
  color: var(--accent);
  line-height: 1;
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

.run-pill:hover:not(:disabled) {
  border-color: var(--accent-border);
  background: var(--surface-selected);
}

.run-pill:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}
</style>
