<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref } from 'vue';
import {
  Plus,
  Search,
  Upload,
  Download,
  Copy,
  Trash2,
  Pencil,
  FlaskConical,
  Rocket,
  Settings,
  RefreshCw,
  ArrowUpCircle,
  ArrowDownCircle,
  FolderOpen,
  Check,
  X,
  ImagePlus,
  RotateCcw,
  CheckSquare,
} from 'lucide-vue-next';
import { apiClient } from '@/api/client';
import { useProjectsStore } from '@/stores/projects';
import { useCanvasStore } from '@/stores/canvas';
import type { CanvasSnapshot, ProjectSummary } from '@/types/canvas';

const emit = defineEmits<{
  (e: 'open-settings'): void;
}>();

const projectsStore = useProjectsStore();
const canvasStore = useCanvasStore();

const busyId = ref<string | null>(null);
const batchBusy = ref(false);
const restarting = ref(false);
const fileInput = ref<HTMLInputElement | null>(null);
const coverInput = ref<HTMLInputElement | null>(null);
const coverTargetId = ref<string | null>(null);

const selectedIds = ref<string[]>([]);
const activeContextId = ref<string | null>(null);
const renamingId = ref<string | null>(null);
const renameDraft = ref('');
const renameInputRef = ref<HTMLInputElement[] | HTMLInputElement | null>(null);
const contextMenu = ref<{
  x: number;
  y: number;
  project: ProjectSummary;
} | null>(null);

const selectedSet = computed(() => new Set(selectedIds.value));
const selectedProjects = computed(() =>
  projectsStore.projects.filter((p) => selectedSet.value.has(p.id)),
);
const selectedTestCount = computed(
  () => selectedProjects.value.filter((p) => p.stage !== 'online').length,
);
const selectedOnlineCount = computed(
  () => selectedProjects.value.filter((p) => p.stage === 'online').length,
);
const visibleProjects = computed(() => {
  if (projectsStore.filter === 'online') return projectsStore.onlineProjects;
  if (projectsStore.filter === 'test') return projectsStore.testProjects;
  return [...projectsStore.onlineProjects, ...projectsStore.testProjects];
});

function isSelected(id: string) {
  return selectedSet.value.has(id);
}

function toggleSelect(id: string) {
  if (selectedSet.value.has(id)) {
    selectedIds.value = selectedIds.value.filter((x) => x !== id);
  } else {
    selectedIds.value = [...selectedIds.value, id];
  }
}

function selectAllVisible() {
  selectedIds.value = visibleProjects.value.map((p) => p.id);
}

function clearSelection() {
  selectedIds.value = [];
}

function closeContextMenu() {
  contextMenu.value = null;
  activeContextId.value = null;
}

function openContextMenu(event: MouseEvent, project: ProjectSummary) {
  event.preventDefault();
  event.stopPropagation();
  activeContextId.value = project.id;
  const menuW = 210;
  const menuH = 340;
  const x = Math.min(event.clientX, Math.max(8, window.innerWidth - menuW - 8));
  const y = Math.min(event.clientY, Math.max(8, window.innerHeight - menuH - 8));
  contextMenu.value = { x, y, project };
}

function handleGlobalKeydown(event: KeyboardEvent) {
  if (event.key === 'Escape') {
    closeContextMenu();
    if (renamingId.value) cancelInlineRename();
  }
}

onMounted(() => {
  window.addEventListener('click', closeContextMenu);
  window.addEventListener('resize', closeContextMenu);
  window.addEventListener('keydown', handleGlobalKeydown);
});

onBeforeUnmount(() => {
  window.removeEventListener('click', closeContextMenu);
  window.removeEventListener('resize', closeContextMenu);
  window.removeEventListener('keydown', handleGlobalKeydown);
});

async function handleRestartServer() {
  if (restarting.value) return;
  restarting.value = true;
  try {
    canvasStore.showToast('正在重启本地后端服务…');
    const ok = await apiClient.restartServerAndWait();
    if (ok) {
      canvasStore.showToast('服务重启完成，正在刷新页面…');
      window.setTimeout(() => window.location.reload(), 300);
    } else {
      canvasStore.showToast('等待服务重连超时，请检查本地 Python 进程后刷新');
      restarting.value = false;
    }
  } catch (err: any) {
    canvasStore.showToast(`重启失败：${err?.message || '未知错误'}`);
    restarting.value = false;
  }
}

async function handleCreateBlank() {
  if (busyId.value) return;
  busyId.value = '__new__';
  try {
    const project = await projectsStore.createBlankProject('未命名画布', 'test');
    canvasStore.loadSnapshot(project.snapshot);
  } catch (err: any) {
    canvasStore.showToast(err?.message || '新建失败');
  } finally {
    busyId.value = null;
  }
}

function handleCardClick(event: MouseEvent, project: ProjectSummary) {
  closeContextMenu();
  if (renamingId.value === project.id) return;
  if (selectedIds.value.length > 0 || event.ctrlKey || event.metaKey) {
    toggleSelect(project.id);
    return;
  }
  void handleOpen(project);
}

async function handleOpen(project: ProjectSummary) {
  closeContextMenu();
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

async function handlePublish(project: ProjectSummary) {
  closeContextMenu();
  if (busyId.value) return;
  busyId.value = project.id;
  try {
    await projectsStore.publishProject(project.id);
    canvasStore.showToast(`已上架「${project.name}」到上线版本`);
  } catch (err: any) {
    canvasStore.showToast(err?.message || '上架失败');
  } finally {
    busyId.value = null;
  }
}

async function handleUnpublish(project: ProjectSummary) {
  closeContextMenu();
  if (busyId.value) return;
  busyId.value = project.id;
  try {
    await projectsStore.unpublishProject(project.id);
    canvasStore.showToast(`已将「${project.name}」下架到测试版本`);
  } catch (err: any) {
    canvasStore.showToast(err?.message || '下架失败');
  } finally {
    busyId.value = null;
  }
}

function startInlineRename(project: ProjectSummary) {
  closeContextMenu();
  renamingId.value = project.id;
  renameDraft.value = project.name;
  nextTick(() => {
    const el = Array.isArray(renameInputRef.value) ? renameInputRef.value[0] : renameInputRef.value;
    if (el) {
      el.focus();
      el.select();
    }
  });
}

function cancelInlineRename() {
  renamingId.value = null;
  renameDraft.value = '';
}

async function commitInlineRename(project: ProjectSummary) {
  if (renamingId.value !== project.id) return;
  const trimmed = renameDraft.value.trim();
  renamingId.value = null;
  renameDraft.value = '';
  if (!trimmed || trimmed === project.name) return;
  try {
    await projectsStore.renameProject(project.id, trimmed);
    canvasStore.showToast('已重命名');
  } catch (err: any) {
    canvasStore.showToast(err?.message || '重命名失败');
  }
}

function triggerChangeCover(project: ProjectSummary) {
  closeContextMenu();
  coverTargetId.value = project.id;
  coverInput.value?.click();
}

async function handleCoverSelected(event: Event) {
  const input = event.target as HTMLInputElement;
  const file = input.files?.[0];
  input.value = '';
  const targetId = coverTargetId.value;
  coverTargetId.value = null;
  if (!file || !targetId) return;
  busyId.value = targetId;
  try {
    const uploaded = await apiClient.uploadAsset(file);
    await projectsStore.updateProjectCover(targetId, uploaded.asset.url);
    canvasStore.showToast('已更新画布预览图');
  } catch (err: any) {
    canvasStore.showToast(`更换预览图失败：${err?.message || '未知错误'}`);
  } finally {
    busyId.value = null;
  }
}

async function handleResetCover(project: ProjectSummary) {
  closeContextMenu();
  busyId.value = project.id;
  try {
    await projectsStore.updateProjectCover(project.id, '');
    canvasStore.showToast('已恢复默认预览图');
  } catch (err: any) {
    canvasStore.showToast(err?.message || '恢复默认预览图失败');
  } finally {
    busyId.value = null;
  }
}

async function handleDuplicate(project: ProjectSummary) {
  closeContextMenu();
  try {
    await projectsStore.duplicateProject(project.id);
    canvasStore.showToast(
      project.stage === 'online' ? '已复制副本到测试版本' : '已复制独立副本',
    );
  } catch (err: any) {
    canvasStore.showToast(err?.message || '复制失败');
  }
}

async function handleDelete(project: ProjectSummary) {
  closeContextMenu();
  const stageLabel = project.stage === 'online' ? '上线版本' : '测试版本';
  if (!window.confirm(`确定删除${stageLabel}画布「${project.name}」吗？此操作不可撤销。`)) return;
  try {
    await projectsStore.deleteProject(project.id);
    selectedIds.value = selectedIds.value.filter((x) => x !== project.id);
    canvasStore.showToast('已删除画布');
  } catch (err: any) {
    canvasStore.showToast(err?.message || '删除失败');
  }
}

function handleExportSelected() {
  if (!selectedIds.value.length || batchBusy.value) return;
  const count = selectedIds.value.length;
  const url = apiClient.exportSelectedProjectsZipUrl(selectedIds.value);
  const a = document.createElement('a');
  a.href = url;
  a.download = count === 1 ? 'canvas.zip' : `rh-canvas-batch-${count}.zip`;
  document.body.appendChild(a);
  a.click();
  a.remove();
  canvasStore.showToast(`正在打包下载选中的 ${count} 个画布 ZIP（含素材）…`);
}

async function handleBatchStage(target: 'online' | 'test') {
  const targets = selectedProjects.value.filter((p) =>
    target === 'online' ? p.stage !== 'online' : p.stage === 'online',
  );
  if (!targets.length || batchBusy.value) return;
  batchBusy.value = true;
  try {
    for (const p of targets) {
      await apiClient.setProjectStage(p.id, target);
    }
    await projectsStore.refreshList();
    canvasStore.showToast(
      target === 'online'
        ? `已批量上架 ${targets.length} 个画布到上线版本`
        : `已批量下架 ${targets.length} 个画布到测试版本`,
    );
    clearSelection();
  } catch (err: any) {
    canvasStore.showToast(err?.message || '批量操作失败');
  } finally {
    batchBusy.value = false;
  }
}

async function handleBatchDelete() {
  if (!selectedIds.value.length || batchBusy.value) return;
  if (!window.confirm(`确定删除选中的 ${selectedIds.value.length} 个画布吗？此操作不可撤销。`)) return;
  batchBusy.value = true;
  try {
    for (const id of [...selectedIds.value]) {
      await apiClient.deleteProject(id);
    }
    await projectsStore.refreshList();
    canvasStore.showToast(`已删除 ${selectedIds.value.length} 个画布`);
    clearSelection();
  } catch (err: any) {
    canvasStore.showToast(err?.message || '批量删除失败');
  } finally {
    batchBusy.value = false;
  }
}

function handleExportSingle(project: ProjectSummary) {
  closeContextMenu();
  const url = apiClient.exportProjectZipUrl(project.id);
  const a = document.createElement('a');
  a.href = url;
  a.download = `${project.name || 'canvas'}.zip`;
  document.body.appendChild(a);
  a.click();
  a.remove();
  canvasStore.showToast(`正在打包导出「${project.name}」…`);
}

async function importSingleFile(file: File) {
  if (
    file.name.toLowerCase().endsWith('.zip') ||
    file.type === 'application/zip' ||
    file.type === 'application/x-zip-compressed'
  ) {
    const res = await projectsStore.importProjectZip(file);
    if (res.mode === 'project' && res.project) {
      return { count: 1, assets: res.imported_assets || 0, name: res.project.name };
    }
    const total = res.imported_projects || res.projects?.length || 0;
    return { count: total, assets: res.imported_assets || 0, name: '' };
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
    throw new Error(`${file.name} 不是有效的画布文件（缺少 canvas.cards）`);
  }
  const project = await projectsStore.importProjectJson(name, snapshot, 'test');
  projectsStore.closeEditor();
  return { count: 1, assets: 0, name: project.name };
}

async function handleImportFile(event: Event) {
  const input = event.target as HTMLInputElement;
  const files = Array.from(input.files || []);
  input.value = '';
  if (!files.length) return;
  try {
    let totalProjects = 0;
    let totalAssets = 0;
    let singleName = '';

    for (const file of files) {
      const r = await importSingleFile(file);
      totalProjects += r.count;
      totalAssets += r.assets;
      if (r.name) singleName = r.name;
    }

    await projectsStore.refreshList();

    if (totalProjects === 1 && singleName) {
      canvasStore.showToast(
        totalAssets > 0
          ? `已导入到测试版本「${singleName}」（还原 ${totalAssets} 份素材）`
          : `已导入到测试版本「${singleName}」`,
      );
    } else {
      const parts = [`${totalProjects} 个画布`];
      if (totalAssets > 0) parts.push(`${totalAssets} 份素材`);
      canvasStore.showToast(`已导入到测试版本：${parts.join('、')}`);
    }
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
          <p>上线版本同步 GitHub，测试版本本地忽略；支持单选/多选导入导出与自定义 2:3 预览图</p>
        </div>
      </div>

      <div class="home-actions">
        <span class="status-pill" :class="{ online: projectsStore.backendOnline }">
          <i class="dot" />
          {{ projectsStore.backendOnline ? '本地服务已连接' : '后端未连接' }}
        </span>
        <button
          class="btn"
          type="button"
          :disabled="restarting"
          title="重启本地 Python 后端服务并刷新页面"
          @click="handleRestartServer"
        >
          <RefreshCw :size="14" :class="{ spin: restarting }" />
          {{ restarting ? '重启中…' : '重启服务' }}
        </button>
        <button class="btn" type="button" @click="emit('open-settings')">
          <Settings :size="15" />
          设置
        </button>
        <button
          class="btn"
          type="button"
          title="导入画布压缩包 ZIP（含素材）或 JSON，支持单选/多选文件"
          @click="fileInput?.click()"
        >
          <Upload :size="15" />
          导入画布
        </button>
        <input
          ref="fileInput"
          type="file"
          accept=".zip,.json,application/zip,application/json"
          multiple
          hidden
          @change="handleImportFile"
        />
        <input
          ref="coverInput"
          type="file"
          accept="image/*"
          hidden
          @change="handleCoverSelected"
        />
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
          全部 ({{ projectsStore.projects.length }})
        </button>
        <button
          class="tab"
          :class="{ active: projectsStore.filter === 'online' }"
          type="button"
          @click="projectsStore.filter = 'online'"
        >
          上线版本 ({{ projectsStore.onlineCount }})
        </button>
        <button
          class="tab"
          :class="{ active: projectsStore.filter === 'test' }"
          type="button"
          @click="projectsStore.filter = 'test'"
        >
          测试版本 ({{ projectsStore.testCount }})
        </button>
      </div>

      <div class="toolbar-right">
        <div class="search-box">
          <Search :size="14" class="search-icon" />
          <input v-model="projectsStore.search" class="field search-input" placeholder="搜索上线版本或测试版本…" />
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

    <!-- 多选批量操作条（仅在勾选画布后显示） -->
    <div v-if="selectedIds.length > 0" class="bulk-bar">
      <div class="bulk-left">
        <span class="bulk-count">已选择 <strong>{{ selectedIds.length }}</strong> 个画布</span>
        <button class="btn" type="button" @click="selectAllVisible">
          全选当前 ({{ visibleProjects.length }})
        </button>
        <button class="btn" type="button" @click="clearSelection">取消选择</button>
      </div>
      <div class="bulk-actions">
        <button
          class="btn primary"
          type="button"
          :disabled="!selectedIds.length || batchBusy"
          @click="handleExportSelected"
        >
          <Download :size="14" />
          导出选中 ZIP ({{ selectedIds.length }})
        </button>
        <button
          v-if="selectedTestCount > 0"
          class="btn"
          type="button"
          :disabled="batchBusy"
          @click="handleBatchStage('online')"
        >
          <ArrowUpCircle :size="14" />
          批量上架 ({{ selectedTestCount }})
        </button>
        <button
          v-if="selectedOnlineCount > 0"
          class="btn"
          type="button"
          :disabled="batchBusy"
          @click="handleBatchStage('test')"
        >
          <ArrowDownCircle :size="14" />
          批量下架 ({{ selectedOnlineCount }})
        </button>
        <button
          class="btn danger"
          type="button"
          :disabled="!selectedIds.length || batchBusy"
          @click="handleBatchDelete"
        >
          <Trash2 :size="14" />
          批量删除
        </button>
      </div>
    </div>

    <main class="home-body" @scroll="closeContextMenu">
      <div v-if="projectsStore.error" class="error-banner">
        <span>{{ projectsStore.error }}</span>
        <button class="btn" type="button" @click="projectsStore.refreshList()">重试</button>
      </div>

      <!-- 1. 上线版本 -->
      <section v-if="projectsStore.filter === 'all' || projectsStore.filter === 'online'" class="home-section">
        <div class="section-head">
          <h2>
            <Rocket :size="15" class="online-icon" />
            上线版本
            <span class="section-count online">{{ projectsStore.onlineProjects.length }}</span>
          </h2>
          <span class="section-sub" :title="projectsStore.stageDirs?.online || 'data/projects/online'">
            目录 data/projects/online · 同步 GitHub · 双击改名，右键可下架或换图
          </span>
        </div>

        <div v-if="projectsStore.loading && !projectsStore.projects.length" class="empty-state">
          正在加载上线版本…
        </div>
        <div v-else-if="!projectsStore.onlineProjects.length" class="empty-state compact">
          <p v-if="projectsStore.search">上线版本中没有匹配「{{ projectsStore.search }}」的画布</p>
          <p v-else>暂无上线版本，点击下方测试画布右上角「↑」或右键选择「上架到上线版本」</p>
          <button v-if="projectsStore.search" class="btn" type="button" @click="projectsStore.search = ''">
            清空搜索
          </button>
        </div>
        <div v-else class="card-grid">
          <article
            v-for="proj in projectsStore.onlineProjects"
            :key="proj.id"
            class="proj-card online-card"
            :class="{
              selected: isSelected(proj.id) || activeContextId === proj.id,
              checked: isSelected(proj.id),
            }"
            @click="handleCardClick($event, proj)"
            @contextmenu="openContextMenu($event, proj)"
          >
            <div class="proj-thumb">
              <img v-if="proj.thumbnail" :src="proj.thumbnail" :alt="proj.name" />
              <div v-else class="proj-placeholder">
                <span>◇</span>
                <small>{{ proj.node_count }} 个节点</small>
              </div>
              <button
                type="button"
                class="select-check"
                :class="{ visible: selectedIds.length > 0 || isSelected(proj.id), active: isSelected(proj.id) }"
                :title="isSelected(proj.id) ? '取消选中' : '选中画布'"
                @click.stop="toggleSelect(proj.id)"
              >
                <Check v-if="isSelected(proj.id)" :size="12" />
              </button>
              <span class="stage-badge online">上线</span>
              <div class="proj-actions" @click.stop>
                <button
                  class="btn icon warn"
                  title="下架到测试版本"
                  type="button"
                  :disabled="busyId === proj.id"
                  @click="handleUnpublish(proj)"
                >
                  <ArrowDownCircle :size="13" />
                </button>
                <button class="btn icon" title="更换预览图" type="button" @click="triggerChangeCover(proj)">
                  <ImagePlus :size="13" />
                </button>
                <button class="btn icon" title="导出画布 ZIP（含素材）" type="button" @click="handleExportSingle(proj)">
                  <Download :size="13" />
                </button>
                <button class="btn icon" title="重命名" type="button" @click="startInlineRename(proj)">
                  <Pencil :size="13" />
                </button>
                <button class="btn icon danger" title="删除" type="button" @click="handleDelete(proj)">
                  <Trash2 :size="13" />
                </button>
              </div>
            </div>
            <div class="proj-meta">
              <div v-if="renamingId === proj.id" class="rename-row" @click.stop @dblclick.stop>
                <input
                  ref="renameInputRef"
                  v-model="renameDraft"
                  class="field rename-input"
                  maxlength="80"
                  @keydown.enter.prevent="commitInlineRename(proj)"
                  @keydown.esc.prevent="cancelInlineRename"
                  @blur="commitInlineRename(proj)"
                />
                <button
                  class="btn icon confirm"
                  type="button"
                  title="确认重命名"
                  @mousedown.prevent="commitInlineRename(proj)"
                >
                  <Check :size="13" />
                </button>
                <button
                  class="btn icon"
                  type="button"
                  title="取消"
                  @mousedown.prevent="cancelInlineRename"
                >
                  <X :size="13" />
                </button>
              </div>
              <strong
                v-else
                class="proj-name"
                :title="`${proj.name}（双击重命名）`"
                @click.stop="selectedIds.length ? toggleSelect(proj.id) : null"
                @dblclick.stop="startInlineRename(proj)"
              >
                {{ proj.name }}
              </strong>
              <div class="proj-sub">
                <span>{{ proj.node_count }} 节点</span>
                <span>{{ formatTime(proj.updated_at) }}</span>
              </div>
            </div>
          </article>
        </div>
      </section>

      <!-- 2. 测试版本 -->
      <section v-if="projectsStore.filter === 'all' || projectsStore.filter === 'test'" class="home-section">
        <div class="section-head">
          <h2>
            <FlaskConical :size="15" class="test-icon" />
            测试版本
            <span class="section-count">{{ projectsStore.testProjects.length }}</span>
          </h2>
          <span class="section-sub" :title="projectsStore.stageDirs?.test || 'data/projects/test'">
            目录 data/projects/test · 本地忽略 · 测试通过后点「↑」或右键上架
          </span>
        </div>

        <div v-if="projectsStore.loading && !projectsStore.projects.length" class="empty-state">
          正在加载测试版本…
        </div>
        <div v-else-if="!projectsStore.testProjects.length" class="empty-state">
          <p v-if="projectsStore.search">测试版本中没有匹配「{{ projectsStore.search }}」的画布</p>
          <p v-else>还没有测试画布，点击右上角「新建空白画布」开始创建和测试</p>
          <button v-if="projectsStore.search" class="btn" type="button" @click="projectsStore.search = ''">
            清空搜索
          </button>
        </div>
        <div v-else class="card-grid">
          <article
            v-for="proj in projectsStore.testProjects"
            :key="proj.id"
            class="proj-card"
            :class="{
              selected: isSelected(proj.id) || activeContextId === proj.id,
              checked: isSelected(proj.id),
            }"
            @click="handleCardClick($event, proj)"
            @contextmenu="openContextMenu($event, proj)"
          >
            <div class="proj-thumb">
              <img v-if="proj.thumbnail" :src="proj.thumbnail" :alt="proj.name" />
              <div v-else class="proj-placeholder">
                <span>◇</span>
                <small>{{ proj.node_count }} 个节点</small>
              </div>
              <button
                type="button"
                class="select-check"
                :class="{ visible: selectedIds.length > 0 || isSelected(proj.id), active: isSelected(proj.id) }"
                :title="isSelected(proj.id) ? '取消选中' : '选中画布'"
                @click.stop="toggleSelect(proj.id)"
              >
                <Check v-if="isSelected(proj.id)" :size="12" />
              </button>
              <div class="proj-actions" @click.stop>
                <button
                  class="btn icon publish"
                  title="上架到上线版本"
                  type="button"
                  :disabled="busyId === proj.id"
                  @click="handlePublish(proj)"
                >
                  <ArrowUpCircle :size="13" />
                </button>
                <button class="btn icon" title="更换预览图" type="button" @click="triggerChangeCover(proj)">
                  <ImagePlus :size="13" />
                </button>
                <button class="btn icon" title="导出画布 ZIP（含素材）" type="button" @click="handleExportSingle(proj)">
                  <Download :size="13" />
                </button>
                <button class="btn icon" title="重命名" type="button" @click="startInlineRename(proj)">
                  <Pencil :size="13" />
                </button>
                <button class="btn icon danger" title="删除" type="button" @click="handleDelete(proj)">
                  <Trash2 :size="13" />
                </button>
              </div>
            </div>
            <div class="proj-meta">
              <div v-if="renamingId === proj.id" class="rename-row" @click.stop @dblclick.stop>
                <input
                  ref="renameInputRef"
                  v-model="renameDraft"
                  class="field rename-input"
                  maxlength="80"
                  @keydown.enter.prevent="commitInlineRename(proj)"
                  @keydown.esc.prevent="cancelInlineRename"
                  @blur="commitInlineRename(proj)"
                />
                <button
                  class="btn icon confirm"
                  type="button"
                  title="确认重命名"
                  @mousedown.prevent="commitInlineRename(proj)"
                >
                  <Check :size="13" />
                </button>
                <button
                  class="btn icon"
                  type="button"
                  title="取消"
                  @mousedown.prevent="cancelInlineRename"
                >
                  <X :size="13" />
                </button>
              </div>
              <strong
                v-else
                class="proj-name"
                :title="`${proj.name}（双击重命名）`"
                @click.stop="selectedIds.length ? toggleSelect(proj.id) : null"
                @dblclick.stop="startInlineRename(proj)"
              >
                {{ proj.name }}
              </strong>
              <div class="proj-sub">
                <span>{{ proj.node_count }} 节点</span>
                <span>{{ formatTime(proj.updated_at) }}</span>
              </div>
            </div>
          </article>
        </div>
      </section>
    </main>

    <!-- 右键菜单 -->
    <Teleport to="body">
      <div
        v-if="contextMenu"
        class="ctx-menu"
        :style="{ left: `${contextMenu.x}px`, top: `${contextMenu.y}px` }"
        @click.stop
        @contextmenu.prevent
      >
        <div class="ctx-header" :title="contextMenu.project.name">
          {{ contextMenu.project.name }}
        </div>
        <button type="button" class="ctx-item" @click="handleOpen(contextMenu.project)">
          <FolderOpen :size="14" />
          <span>打开画布</span>
        </button>
        <button
          v-if="contextMenu.project.stage !== 'online'"
          type="button"
          class="ctx-item accent"
          @click="handlePublish(contextMenu.project)"
        >
          <ArrowUpCircle :size="14" />
          <span>上架到上线版本</span>
        </button>
        <button
          v-else
          type="button"
          class="ctx-item warn"
          @click="handleUnpublish(contextMenu.project)"
        >
          <ArrowDownCircle :size="14" />
          <span>下架到测试版本</span>
        </button>
        <div class="ctx-sep" />
        <button type="button" class="ctx-item" @click="triggerChangeCover(contextMenu.project)">
          <ImagePlus :size="14" />
          <span>更换预览图</span>
        </button>
        <button
          v-if="contextMenu.project.has_custom_cover"
          type="button"
          class="ctx-item"
          @click="handleResetCover(contextMenu.project)"
        >
          <RotateCcw :size="14" />
          <span>恢复默认预览图</span>
        </button>
        <button type="button" class="ctx-item" @click="startInlineRename(contextMenu.project)">
          <Pencil :size="14" />
          <span>重命名</span>
        </button>
        <button type="button" class="ctx-item" @click="handleDuplicate(contextMenu.project)">
          <Copy :size="14" />
          <span>{{ contextMenu.project.stage === 'online' ? '复制副本到测试版本' : '复制副本' }}</span>
        </button>
        <button type="button" class="ctx-item" @click="handleExportSingle(contextMenu.project)">
          <Download :size="14" />
          <span>导出画布 ZIP</span>
        </button>
        <button
          type="button"
          class="ctx-item"
          @click="
            toggleSelect(contextMenu.project.id);
            closeContextMenu();
          "
        >
          <CheckSquare :size="14" />
          <span>{{ isSelected(contextMenu.project.id) ? '取消选中' : '选中画布（多选）' }}</span>
        </button>
        <div class="ctx-sep" />
        <button type="button" class="ctx-item danger" @click="handleDelete(contextMenu.project)">
          <Trash2 :size="14" />
          <span>删除画布</span>
        </button>
      </div>
    </Teleport>
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

.btn-active {
  border-color: var(--accent-border);
  background: var(--accent-soft);
  color: var(--accent);
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

.bulk-bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 10px 26px;
  background: rgba(204, 255, 0, 0.06);
  border-bottom: 1px solid var(--accent-border);
}

.bulk-left,
.bulk-actions {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.bulk-count {
  font-size: 13px;
  color: var(--text);
  margin-right: 4px;
}

.bulk-count strong {
  color: var(--accent);
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
  gap: 12px;
  margin-bottom: 14px;
}

.section-head h2 {
  margin: 0;
  font-size: 15px;
  display: inline-flex;
  align-items: center;
  gap: 7px;
}

.online-icon {
  color: var(--accent);
}

.test-icon {
  color: #62b5ff;
}

.section-count {
  font-size: 11px;
  font-weight: 600;
  padding: 1px 7px;
  border-radius: 999px;
  background: var(--panel-2);
  border: 1px solid var(--line);
  color: var(--muted);
}

.section-count.online {
  background: var(--accent-soft);
  border-color: var(--accent-border);
  color: var(--accent);
}

.section-sub {
  color: var(--muted);
  font-size: 12px;
}

.card-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(192px, 224px));
  gap: 16px;
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

.proj-card.online-card {
  border-color: rgba(204, 255, 0, 0.22);
}

.proj-card:hover {
  transform: translateY(-2px);
  border-color: var(--accent-border);
  box-shadow: var(--shadow);
}

.proj-card.selected {
  border-color: var(--accent);
  box-shadow: 0 0 0 2px var(--accent-soft), var(--shadow);
}

.proj-thumb {
  position: relative;
  width: 100%;
  aspect-ratio: 2 / 3;
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
  font-size: 36px;
  color: var(--accent);
  opacity: 0.7;
}

.select-check {
  position: absolute;
  top: 8px;
  left: 8px;
  z-index: 3;
  width: 22px;
  height: 22px;
  border-radius: 6px;
  border: 1.5px solid rgba(255, 255, 255, 0.55);
  background: rgba(17, 19, 23, 0.72);
  color: #111317;
  display: grid;
  place-items: center;
  opacity: 0;
  cursor: pointer;
  transition: opacity 0.14s ease, background 0.14s ease, border-color 0.14s ease;
}

.proj-card:hover .select-check,
.select-check.visible {
  opacity: 1;
}

.select-check.active {
  background: var(--accent);
  border-color: var(--accent);
  color: #111317;
}

.stage-badge {
  position: absolute;
  bottom: 8px;
  left: 8px;
  padding: 2px 8px;
  border-radius: 999px;
  font-size: 11px;
  font-weight: 600;
  backdrop-filter: blur(8px);
}

.stage-badge.online {
  background: rgba(20, 24, 16, 0.82);
  border: 1px solid rgba(204, 255, 0, 0.45);
  color: var(--accent);
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
  width: 27px;
  min-height: 27px;
  padding: 0;
  background: rgba(17, 19, 23, 0.85);
}

.proj-actions .btn.publish {
  color: var(--accent);
  border-color: rgba(204, 255, 0, 0.35);
}

.proj-actions .btn.warn {
  color: #f6c177;
  border-color: rgba(246, 193, 119, 0.35);
}

.proj-meta {
  padding: 10px 12px;
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.proj-meta strong.proj-name {
  font-size: 13px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  padding: 2px 4px;
  margin: -2px -4px;
  border-radius: 6px;
  cursor: text;
  transition: background 0.14s ease;
}

.proj-meta strong.proj-name:hover {
  background: rgba(255, 255, 255, 0.06);
}

.rename-row {
  display: flex;
  align-items: center;
  gap: 4px;
}

.rename-input {
  flex: 1;
  min-width: 0;
  height: 26px;
  padding: 2px 7px;
  font-size: 12.5px;
  font-weight: 600;
}

.rename-row .btn.icon {
  width: 24px;
  height: 24px;
  min-height: 24px;
  flex-shrink: 0;
}

.rename-row .btn.icon.confirm {
  color: var(--accent);
  border-color: var(--accent-border);
  background: var(--accent-soft);
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

.empty-state.compact {
  padding: 28px 20px;
}

.ctx-menu {
  position: fixed;
  z-index: 1200;
  min-width: 196px;
  padding: 6px;
  border-radius: 12px;
  background: rgba(24, 26, 31, 0.96);
  border: 1px solid var(--line-strong);
  box-shadow: 0 14px 36px rgba(0, 0, 0, 0.55);
  backdrop-filter: blur(18px);
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.ctx-header {
  padding: 5px 10px 7px;
  font-size: 11px;
  color: var(--muted);
  border-bottom: 1px solid var(--line);
  margin-bottom: 4px;
  max-width: 220px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.ctx-item {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
  padding: 7px 10px;
  border: 0;
  border-radius: 8px;
  background: transparent;
  color: var(--text);
  font-size: 12.5px;
  text-align: left;
  cursor: pointer;
}

.ctx-item:hover {
  background: rgba(255, 255, 255, 0.08);
}

.ctx-item.accent {
  color: var(--accent);
  font-weight: 600;
}

.ctx-item.accent:hover {
  background: var(--accent-soft);
}

.ctx-item.warn {
  color: #f6c177;
  font-weight: 600;
}

.ctx-item.warn:hover {
  background: rgba(246, 193, 119, 0.14);
}

.ctx-item.danger {
  color: var(--danger);
}

.ctx-item.danger:hover {
  background: rgba(255, 123, 114, 0.14);
}

.ctx-sep {
  height: 1px;
  background: var(--line);
  margin: 4px 6px;
}

.spin {
  animation: spin 0.9s linear infinite;
}

@keyframes spin {
  to {
    transform: rotate(360deg);
  }
}
</style>
