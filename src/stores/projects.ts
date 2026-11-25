import { defineStore } from 'pinia';
import { ref, computed } from 'vue';
import { apiClient } from '@/api/client';
import type { CanvasSnapshot, ProjectRecord, ProjectStage, ProjectSummary } from '@/types/canvas';

export type HomeFilter = 'all' | 'online' | 'test';
export type HomeSort = 'updated' | 'name';

export const useProjectsStore = defineStore('projects', () => {
  const projects = ref<ProjectSummary[]>([]);
  const stageDirs = ref<{ online: string; test: string } | null>(null);
  const loading = ref(false);
  const error = ref<string | null>(null);
  const backendOnline = ref(false);
  const backendInfo = ref<{ output_dir: string; has_runninghub_key: boolean; has_llm_key: boolean } | null>(null);

  const search = ref('');
  const filter = ref<HomeFilter>('all');
  const sortBy = ref<HomeSort>('updated');
  const activeProjectId = ref<string | null>(null);
  const activeProjectName = ref('未命名画布');
  const activeProjectStage = ref<ProjectStage>('test');
  const saving = ref(false);
  const lastSavedAt = ref<string | null>(null);

  function sortAndFilter(items: ProjectSummary[]) {
    const q = search.value.trim().toLowerCase();
    let list = items.filter((p) => !q || p.name.toLowerCase().includes(q));
    if (sortBy.value === 'name') {
      list = [...list].sort((a, b) => a.name.localeCompare(b.name, 'zh-CN'));
    } else {
      list = [...list].sort((a, b) => (b.updated_at || '').localeCompare(a.updated_at || ''));
    }
    return list;
  }

  const onlineProjects = computed(() =>
    sortAndFilter(projects.value.filter((p) => p.stage === 'online')),
  );

  const testProjects = computed(() =>
    sortAndFilter(projects.value.filter((p) => p.stage !== 'online')),
  );

  const onlineCount = computed(() => projects.value.filter((p) => p.stage === 'online').length);
  const testCount = computed(() => projects.value.filter((p) => p.stage !== 'online').length);

  async function checkHealth() {
    try {
      const res = await apiClient.health();
      backendOnline.value = true;
      backendInfo.value = {
        output_dir: res.output_dir,
        has_runninghub_key: res.has_runninghub_key,
        has_llm_key: res.has_llm_key,
      };
    } catch {
      backendOnline.value = false;
      backendInfo.value = null;
    }
  }

  async function refreshList() {
    loading.value = true;
    error.value = null;
    try {
      await checkHealth();
      const res = await apiClient.listProjects();
      projects.value = res.projects.map((p) => ({
        ...p,
        stage: p.stage === 'online' ? 'online' : 'test',
      }));
      if (res.dirs) {
        stageDirs.value = res.dirs;
      }
    } catch (err: any) {
      error.value = err?.message || '加载画布列表失败';
    } finally {
      loading.value = false;
    }
  }

  async function openProject(id: string): Promise<ProjectRecord> {
    const res = await apiClient.getProject(id);
    activeProjectId.value = res.project.id;
    activeProjectName.value = res.project.name;
    activeProjectStage.value = res.project.stage === 'online' ? 'online' : 'test';
    lastSavedAt.value = res.project.updated_at;
    return res.project;
  }

  async function createBlankProject(name = '未命名画布', stage: ProjectStage = 'test'): Promise<ProjectRecord> {
    const res = await apiClient.createProject({ name, stage });
    activeProjectId.value = res.project.id;
    activeProjectName.value = res.project.name;
    activeProjectStage.value = res.project.stage === 'online' ? 'online' : 'test';
    lastSavedAt.value = res.project.updated_at;
    await refreshList();
    return res.project;
  }

  async function importProjectJson(
    name: string,
    snapshot: CanvasSnapshot,
    stage: ProjectStage = 'test',
  ): Promise<ProjectRecord> {
    const res = await apiClient.createProject({ name, snapshot, stage });
    activeProjectId.value = res.project.id;
    activeProjectName.value = res.project.name;
    activeProjectStage.value = res.project.stage === 'online' ? 'online' : 'test';
    lastSavedAt.value = res.project.updated_at;
    await refreshList();
    return res.project;
  }

  async function importProjectZip(file: File) {
    const res = await apiClient.importWorkspaceZip(file);
    if (res.mode === 'project' && res.project) {
      activeProjectId.value = res.project.id;
      activeProjectName.value = res.project.name;
      activeProjectStage.value = res.project.stage === 'online' ? 'online' : 'test';
      lastSavedAt.value = res.project.updated_at;
    }
    await refreshList();
    return res;
  }

  async function saveActive(snapshot: CanvasSnapshot, name?: string) {
    if (!activeProjectId.value) return;
    saving.value = true;
    try {
      const res = await apiClient.updateProject(activeProjectId.value, {
        name: name ?? activeProjectName.value,
        snapshot,
      });
      activeProjectName.value = res.project.name;
      activeProjectStage.value = res.project.stage === 'online' ? 'online' : 'test';
      lastSavedAt.value = res.project.updated_at;
      const idx = projects.value.findIndex((p) => p.id === res.project.id);
      const summary: ProjectSummary = {
        schema_version: res.project.schema_version,
        id: res.project.id,
        name: res.project.name,
        stage: res.project.stage === 'online' ? 'online' : 'test',
        created_at: res.project.created_at,
        updated_at: res.project.updated_at,
        node_count: res.project.node_count,
      };
      if (idx >= 0) projects.value[idx] = { ...projects.value[idx], ...summary };
      else projects.value.unshift(summary);
    } finally {
      saving.value = false;
    }
  }

  async function renameProject(id: string, name: string) {
    const res = await apiClient.updateProject(id, { name });
    if (activeProjectId.value === id) {
      activeProjectName.value = res.project.name;
    }
    await refreshList();
  }

  async function updateProjectCover(id: string, cover: string) {
    await apiClient.updateProject(id, { cover });
    await refreshList();
  }

  async function moveProjectStage(id: string, stage: ProjectStage) {
    const res = await apiClient.setProjectStage(id, stage);
    if (activeProjectId.value === id) {
      activeProjectStage.value = res.project.stage === 'online' ? 'online' : 'test';
    }
    await refreshList();
    return res.project;
  }

  async function publishProject(id: string) {
    return moveProjectStage(id, 'online');
  }

  async function unpublishProject(id: string) {
    return moveProjectStage(id, 'test');
  }

  async function duplicateProject(id: string) {
    await apiClient.duplicateProject(id);
    await refreshList();
  }

  async function deleteProject(id: string) {
    await apiClient.deleteProject(id);
    if (activeProjectId.value === id) {
      activeProjectId.value = null;
    }
    await refreshList();
  }

  function closeEditor() {
    activeProjectId.value = null;
  }

  return {
    projects,
    stageDirs,
    loading,
    error,
    backendOnline,
    backendInfo,
    search,
    filter,
    sortBy,
    activeProjectId,
    activeProjectName,
    activeProjectStage,
    saving,
    lastSavedAt,
    onlineProjects,
    testProjects,
    onlineCount,
    testCount,
    checkHealth,
    refreshList,
    openProject,
    createBlankProject,
    importProjectJson,
    importProjectZip,
    saveActive,
    renameProject,
    updateProjectCover,
    moveProjectStage,
    publishProject,
    unpublishProject,
    duplicateProject,
    deleteProject,
    closeEditor,
  };
});
