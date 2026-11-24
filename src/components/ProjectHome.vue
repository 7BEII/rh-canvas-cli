<script setup lang="ts">
import { ref } from 'vue';
import {
  Plus,
  Search,
  Upload,
  Copy,
  Trash2,
  Pencil,
  Layers,
  Sparkles,
  Settings,
  RefreshCw,
} from 'lucide-vue-next';
import { useProjectsStore } from '@/stores/projects';
import { useCanvasStore } from '@/stores/canvas';
import type { CanvasSnapshot, PresetTemplate, ProjectSummary } from '@/types/canvas';

const emit = defineEmits<{
  (e: 'open-settings'): void;
}>();

const projectsStore = useProjectsStore();
const canvasStore = useCanvasStore();

const busyId = ref<string | null>(null);
const fileInput = ref<HTMLInputElement | null>(null);

async function handleCreateBlank() {
  if (busyId.value) return;
  busyId.value = '__new__';
  try {
    const project = await projectsStore.createBlankProject();
    canvasStore.loadSnapshot(project.snapshot);
  } catch (err: any) {
    canvasStore.showToast(err?.message || '新建失败');
  } finally {
    busyId.value = null;
  }
}

async function handleUseTemplate(tpl: PresetTemplate) {
  if (busyId.value) return;
  busyId.value = tpl.id;
  try {
    const project = await projectsStore.createFromTemplate(tpl);
    canvasStore.loadSnapshot(project.snapshot);
    canvasStore.showToast(`已从模板「${tpl.name}」创建独立副本`);
  } catch (err: any) {
    canvasStore.showToast(err?.message || '模板创建失败');
  } finally {
    busyId.value = null;
  }
}

async function handleOpen(project: ProjectSummary) {
  if (busyId.value) return;
  busyId.value = project.id;
  try {
    const full = await projectsStore.openProject(project.id);
    canvasStore.loadSnapshot(full.snapshot);
  } catch (err: any) {
    canvasStore.showToast(err?.message || '打开画布失败');
    await projectsStore.refreshList();
  } finally {
    busyId.value = null;
  }
}

async function handleRename(project: ProjectSummary) {
  const next = window.prompt('重命名画布', project.name);
  if (!next || !next.trim() || next.trim() === project.name) return;
  try {
    await projectsStore.renameProject(project.id, next.trim());
    canvasStore.showToast('已重命名');
  } catch (err: any) {
    canvasStore.showToast(err?.message || '重命名失败');
  }
}

async function handleDuplicate(project: ProjectSummary) {
  try {
    await projectsStore.duplicateProject(project.id);
    canvasStore.showToast('已复制独立副本');
  } catch (err: any) {
    canvasStore.showToast(err?.message || '复制失败');
  }
}

async function handleDelete(project: ProjectSummary) {
  if (!window.confirm(`确定删除画布「${project.name}」吗？此操作不可撤销。`)) return;
  try {
    await projectsStore.deleteProject(project.id);
    canvasStore.showToast('已删除画布');
  } catch (err: any) {
    canvasStore.showToast(err?.message || '删除失败');
  }
}

async function handleImportFile(event: Event) {
  const input = event.target as HTMLInputElement;
  const file = input.files?.[0];
  input.value = '';
  if (!file) return;
  try {
    if (file.name.toLowerCase().endsWith('.zip') || file.type === 'application/zip' || file.type === 'application/x-zip-compressed') {
      const { project, importedAssets } = await projectsStore.importProjectZip(file);
      canvasStore.loadSnapshot(project.snapshot);
      canvasStore.showToast(
        importedAssets > 0
          ? `已导入画布「${project.name}」（还原 ${importedAssets} 份素材）`
          : `已导入画布「${project.name}」`,
      );
      return;
    }
    const raw = JSON.parse(await file.text());
    let snapshot: CanvasSnapshot;
    let name = file.name.replace(/\.json$/i, '');
    if (raw.snapshot?.canvas?.cards) {
      snapshot = raw.snapshot;
      if (raw.name) name = raw.name;
    } else if (raw.canvas?.cards) {
      snapshot = raw;
    } else if (Array.isArray(raw.cards)) {
      snapshot = {
        format: 'rh-canvas',
        version: 1,
        exportedAt: new Date().toISOString(),
        schemaVersion: 1,
        canvas: { cards: raw.cards, seq: raw.cards.length + 1, viewport: raw.viewport || { x: 60, y: 60, z: 0.9 } },
      };
    } else {
      throw new Error('JSON 格式不兼容，缺少 canvas.cards 字段');
    }
    const project = await projectsStore.importProjectJson(name, snapshot);
    canvasStore.loadSnapshot(project.snapshot);
    canvasStore.showToast(`已导入画布「${project.name}」`);
  } catch (err: any) {
    canvasStore.showToast(`导入失败：${err?.message || '无效文件'}`);
  }
}

function formatTime(iso: string) {
  if (!iso) return '刚刚';
  try {
    return new Date(iso).toLocaleString('zh-CN', {
      month: '2-digit',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
    });
  } catch {
    return iso;
  }
}
</script>

<template>
  <div class="home-shell">
    <header class="home-header">
      <div class="brand">
        <div class="logo" aria-hidden="true">
          <svg viewBox="0 0 28 28" width="22" height="22" fill="none">
            <rect x="3" y="4.5" width="8" height="8" rx="2.5" stroke="#ffffff" stroke-opacity="0.9" stroke-width="1.8" />
            <rect x="3" y="15.5" width="8" height="8" rx="2.5" stroke="#ffffff" stroke-opacity="0.55" stroke-width="1.8" />
            <rect x="17" y="9.5" width="8" height="9" rx="2.5" fill="#ccff00" />
            <path d="M11 8.5C14.2 8.5 13.8 14 17 14" stroke="#ccff00" stroke-width="1.8" stroke-linecap="round" />
            <path d="M11 19.5C14.2 19.5 13.8 14 17 14" stroke="#ffffff" stroke-opacity="0.45" stroke-width="1.6" stroke-linecap="round" />
          </svg>
        </div>
        <div>
          <h1>全部画布</h1>
          <p>选择预设模板开始创作，或继续编辑已有画布（独立 Vue 3 + Python 服务）</p>
        </div>
      </div>

      <div class="home-actions">
        <span class="status-pill" :class="{ online: projectsStore.backendOnline }">
          <i class="dot" />
          {{ projectsStore.backendOnline ? '本地服务已连接' : '后端未连接' }}
        </span>
        <button class="btn" type="button" @click="emit('open-settings')">
          <Settings :size="15" />
          设置
        </button>
        <button class="btn" type="button" @click="fileInput?.click()">
          <Upload :size="15" />
          导入 ZIP / JSON
        </button>
        <input ref="fileInput" type="file" accept=".zip,.json,application/zip,application/json" hidden @change="handleImportFile" />
        <button class="btn primary" type="button" :disabled="busyId !== null" @click="handleCreateBlank">
          <Plus :size="16" />
          新建空白画布
        </button>
      </div>
    </header>

    <div class="home-toolbar">
      <div class="filter-tabs">
        <button
          class="tab"
          :class="{ active: projectsStore.filter === 'all' }"
          type="button"
          @click="projectsStore.filter = 'all'"
        >
          全部
        </button>
        <button
          class="tab"
          :class="{ active: projectsStore.filter === 'templates' }"
          type="button"
          @click="projectsStore.filter = 'templates'"
        >
          预设模板
        </button>
        <button
          class="tab"
          :class="{ active: projectsStore.filter === 'mine' }"
          type="button"
          @click="projectsStore.filter = 'mine'"
        >
          我的画布 ({{ projectsStore.projects.length }})
        </button>
      </div>

      <div class="toolbar-right">
        <div class="search-box">
          <Search :size="14" class="search-icon" />
          <input v-model="projectsStore.search" class="field search-input" placeholder="搜索模板或我的画布…" />
        </div>
        <select v-model="projectsStore.sortBy" class="field sort-select">
          <option value="updated">按最近修改</option>
          <option value="name">按名称排序</option>
        </select>
        <button class="btn icon" type="button" title="刷新列表" @click="projectsStore.refreshList()">
          <RefreshCw :size="14" />
        </button>
      </div>
    </div>

    <main class="home-body">
      <div v-if="projectsStore.error" class="error-banner">
        <span>{{ projectsStore.error }}</span>
        <button class="btn" type="button" @click="projectsStore.refreshList()">重试</button>
      </div>

      <section
        v-if="(projectsStore.filter === 'all' || projectsStore.filter === 'templates') && projectsStore.filteredTemplates.length"
        class="home-section"
      >
        <div class="section-head">
          <h2><Sparkles :size="15" /> 预设模板</h2>
          <span class="section-sub">每次使用都会创建独立副本，不影响原模板</span>
        </div>
        <div class="card-grid">
          <article
            v-for="tpl in projectsStore.filteredTemplates"
            :key="tpl.id"
            class="tpl-card"
            @click="handleUseTemplate(tpl)"
          >
            <div class="tpl-top">
              <span class="tpl-badge" :style="{ color: tpl.accent, borderColor: tpl.accent + '55' }">
                {{ tpl.badge }}
              </span>
              <button class="btn primary use-btn" type="button" :disabled="busyId === tpl.id">
                {{ busyId === tpl.id ? '创建中…' : '使用模板' }}
              </button>
            </div>
            <h3>{{ tpl.name }}</h3>
            <p>{{ tpl.description }}</p>
            <div class="tpl-steps">
              <span v-for="(step, idx) in tpl.steps" :key="step" class="step-chip">
                {{ step }}<em v-if="idx < tpl.steps.length - 1">→</em>
              </span>
            </div>
          </article>
        </div>
      </section>

      <section v-if="projectsStore.filter === 'all' || projectsStore.filter === 'mine'" class="home-section">
        <div class="section-head">
          <h2><Layers :size="15" /> 我的画布</h2>
          <span class="section-sub">单击卡片直接进入无限画布编辑器</span>
        </div>

        <div v-if="projectsStore.loading && !projectsStore.projects.length" class="empty-state">
          正在加载画布列表…
        </div>
        <div v-else-if="!projectsStore.filteredProjects.length" class="empty-state">
          <p v-if="projectsStore.search">没有匹配「{{ projectsStore.search }}」的画布</p>
          <p v-else>还没有个人画布，点击上方预设模板或「新建空白画布」开始创作</p>
          <button v-if="projectsStore.search" class="btn" type="button" @click="projectsStore.search = ''">
            清空搜索
          </button>
        </div>
        <div v-else class="card-grid">
          <article
            v-for="proj in projectsStore.filteredProjects"
            :key="proj.id"
            class="proj-card"
            @click="handleOpen(proj)"
          >
            <div class="proj-thumb">
              <img v-if="proj.thumbnail" :src="proj.thumbnail" :alt="proj.name" />
              <div v-else class="proj-placeholder">
                <span>◇</span>
                <small>{{ proj.node_count }} 个节点</small>
              </div>
              <div class="proj-actions" @click.stop>
                <button class="btn icon" title="重命名" type="button" @click="handleRename(proj)">
                  <Pencil :size="13" />
                </button>
                <button class="btn icon" title="复制副本" type="button" @click="handleDuplicate(proj)">
                  <Copy :size="13" />
                </button>
                <button class="btn icon danger" title="删除" type="button" @click="handleDelete(proj)">
                  <Trash2 :size="13" />
                </button>
              </div>
            </div>
            <div class="proj-meta">
              <strong :title="proj.name">{{ proj.name }}</strong>
              <div class="proj-sub">
                <span>{{ proj.node_count }} 节点</span>
                <span>最近修改 {{ formatTime(proj.updated_at) }}</span>
              </div>
            </div>
          </article>
        </div>
      </section>
    </main>
  </div>
</template>

<style scoped>
.home-shell {
  height: 100%;
  display: flex;
  flex-direction: column;
  background: var(--bg);
}

.home-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  padding: 18px 26px;
  border-bottom: 1px solid var(--line);
  background: var(--panel);
}

.brand {
  display: flex;
  align-items: center;
  gap: 14px;
}

.logo {
  width: 40px;
  height: 40px;
  border-radius: 12px;
  background: linear-gradient(145deg, #23252a 0%, #141518 100%);
  border: 1px solid rgba(204, 255, 0, 0.32);
  box-shadow:
    inset 0 1px 0 rgba(255, 255, 255, 0.08),
    0 6px 18px rgba(0, 0, 0, 0.45),
    0 0 16px rgba(204, 255, 0, 0.12);
  display: grid;
  place-items: center;
  flex-shrink: 0;
}

.brand h1 {
  margin: 0;
  font-size: 19px;
}

.brand p {
  margin: 2px 0 0;
  color: var(--muted);
  font-size: 12px;
}

.home-actions {
  display: flex;
  align-items: center;
  gap: 8px;
}

.status-pill {
  display: inline-flex;
  align-items: center;
  gap: 7px;
  padding: 5px 11px;
  border-radius: 999px;
  border: 1px solid var(--line);
  background: var(--panel-2);
  color: var(--muted);
  font-size: 12px;
  white-space: nowrap;
  flex-shrink: 0;
}

.status-pill .dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: var(--danger);
}

.status-pill.online .dot {
  background: var(--accent);
  box-shadow: 0 0 0 3px var(--accent-soft);
}

.home-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 14px 26px;
  border-bottom: 1px solid var(--line);
}

.filter-tabs {
  display: flex;
  gap: 6px;
  padding: 3px;
  border-radius: 10px;
  background: var(--panel);
  border: 1px solid var(--line);
}

.tab {
  border: 0;
  background: transparent;
  color: var(--muted);
  padding: 6px 13px;
  border-radius: 8px;
  white-space: nowrap;
  flex-shrink: 0;
}

.tab.active {
  background: var(--accent-soft);
  color: var(--accent);
  font-weight: 600;
}

.toolbar-right {
  display: flex;
  align-items: center;
  gap: 8px;
}

.search-box {
  position: relative;
  width: 240px;
}

.search-icon {
  position: absolute;
  left: 10px;
  top: 50%;
  transform: translateY(-50%);
  color: var(--muted);
}

.search-input {
  padding-left: 30px;
}

.sort-select {
  width: 132px;
}

.home-body {
  flex: 1;
  overflow: auto;
  padding: 22px 26px 40px;
}

.error-banner {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 12px 16px;
  margin-bottom: 18px;
  border-radius: 12px;
  background: rgba(255, 123, 114, 0.12);
  border: 1px solid rgba(255, 123, 114, 0.35);
  color: var(--danger);
}

.home-section + .home-section {
  margin-top: 28px;
}

.section-head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  margin-bottom: 14px;
}

.section-head h2 {
  margin: 0;
  font-size: 15px;
  display: inline-flex;
  align-items: center;
  gap: 7px;
}

.section-sub {
  color: var(--muted);
  font-size: 12px;
}

.card-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
  gap: 14px;
}

.tpl-card {
  display: flex;
  flex-direction: column;
  gap: 10px;
  padding: 16px;
  border-radius: 14px;
  background: var(--panel);
  border: 1px solid var(--line);
  cursor: pointer;
  transition: transform 0.16s ease, border-color 0.16s ease, box-shadow 0.16s ease;
}

.tpl-card:hover {
  transform: translateY(-2px);
  border-color: var(--accent-border);
  box-shadow: var(--shadow);
}

.tpl-top {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.tpl-badge {
  font-size: 11px;
  padding: 3px 8px;
  border-radius: 999px;
  border: 1px solid var(--line);
  background: rgba(255, 255, 255, 0.03);
}

.use-btn {
  min-height: 28px;
  padding: 0 10px;
  font-size: 12px;
}

.tpl-card h3 {
  margin: 0;
  font-size: 15px;
}

.tpl-card p {
  margin: 0;
  color: var(--muted);
  font-size: 12px;
  flex: 1;
}

.tpl-steps {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
  padding: 8px 10px;
  border-radius: 9px;
  background: var(--bg);
  font-size: 11px;
  color: var(--muted);
}

.step-chip em {
  font-style: normal;
  margin: 0 4px;
  color: var(--accent);
}

.proj-card {
  display: flex;
  flex-direction: column;
  border-radius: 14px;
  overflow: hidden;
  background: var(--panel);
  border: 1px solid var(--line);
  cursor: pointer;
  transition: transform 0.16s ease, border-color 0.16s ease, box-shadow 0.16s ease;
}

.proj-card:hover {
  transform: translateY(-2px);
  border-color: var(--accent-border);
  box-shadow: var(--shadow);
}

.proj-thumb {
  position: relative;
  height: 148px;
  background: radial-gradient(circle at 20% 20%, rgba(116, 213, 139, 0.14), transparent 60%), var(--panel-2);
  display: grid;
  place-items: center;
  overflow: hidden;
}

.proj-thumb img {
  width: 100%;
  height: 100%;
  object-fit: cover;
}

.proj-placeholder {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 4px;
  color: var(--muted);
}

.proj-placeholder span {
  font-size: 32px;
  color: var(--accent);
  opacity: 0.7;
}

.proj-actions {
  position: absolute;
  top: 8px;
  right: 8px;
  display: flex;
  gap: 4px;
  opacity: 0;
  transition: opacity 0.15s ease;
}

.proj-card:hover .proj-actions {
  opacity: 1;
}

.proj-actions .btn {
  width: 28px;
  min-height: 28px;
  background: rgba(17, 19, 23, 0.85);
}

.proj-meta {
  padding: 12px 14px;
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.proj-meta strong {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.proj-sub {
  display: flex;
  justify-content: space-between;
  color: var(--muted);
  font-size: 11px;
}

.empty-state {
  padding: 46px 20px;
  border-radius: 14px;
  border: 1px dashed var(--line-strong);
  text-align: center;
  color: var(--muted);
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 10px;
}
</style>
