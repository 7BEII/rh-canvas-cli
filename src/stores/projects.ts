import { defineStore } from 'pinia';
import { ref, computed } from 'vue';
import { apiClient } from '@/api/client';
import { PRESET_TEMPLATES } from '@/data/templates';
import type { CanvasSnapshot, PresetTemplate, ProjectRecord, ProjectSummary } from '@/types/canvas';

export type HomeFilter = 'all' | 'templates' | 'mine';
export type HomeSort = 'updated' | 'name';

export const useProjectsStore = defineStore('projects', () => {
  const projects = ref<ProjectSummary[]>([]);
  const loading = ref(false);
  const error = ref<string | null>(null);
  const backendOnline = ref(false);
  const backendInfo = ref<{ output_dir: string; has_runninghub_key: boolean; has_llm_key: boolean } | null>(null);

  const search = ref('');
  const filter = ref<HomeFilter>('all');
  const sortBy = ref<HomeSort>('updated');
  const activeProjectId = ref<string | null>(null);
  const activeProjectName = ref('未命名画布');
  const saving = ref(false);
  const lastSavedAt = ref<string | null>(null);

  const filteredTemplates = computed(() => {
    const q = search.value.trim().toLowerCase();
    if (!q) return PRESET_TEMPLATES;
    return PRESET_TEMPLATES.filter(
      (t) => t.name.toLowerCase().includes(q) || t.description.toLowerCase().includes(q),
    );
  });

  const filteredProjects = computed(() => {
    const q = search.value.trim().toLowerCase();
    let list = projects.value.filter((p) => !q || p.name.toLowerCase().includes(q));
    if (sortBy.value === 'name') {
      list = [...list].sort((a, b) => a.name.localeCompare(b.name, 'zh-CN'));
    } else {
      list = [...list].sort((a, b) => (b.updated_at || '').localeCompare(a.updated_at || ''));
    }
    return list;
  });

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
      projects.value = res.projects;
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
    lastSavedAt.value = res.project.updated_at;
    return res.project;
  }

  async function createBlankProject(name = '未命名画布'): Promise<ProjectRecord> {
    const res = await apiClient.createProject({ name });
    activeProjectId.value = res.project.id;
    activeProjectName.value = res.project.name;
    lastSavedAt.value = res.project.updated_at;
    await refreshList();
    return res.project;
  }

  async function createFromTemplate(tpl: PresetTemplate): Promise<ProjectRecord> {
    const built = tpl.build();
    const snapshot: CanvasSnapshot = {
      format: 'rh-canvas',
      version: 1,
      exportedAt: new Date().toISOString(),
      schemaVersion: 1,
      canvas: {
        cards: built.cards,
        seq: built.cards.length + 1,
        viewport: built.viewport,
      },
    };
    const existingNames = new Set(projects.value.map((p) => p.name));
    let finalName = tpl.name;
    let suffix = 2;
    while (existingNames.has(finalName)) {
      finalName = `${tpl.name} (${suffix++})`;
    }
    const res = await apiClient.createProject({ name: finalName, snapshot });
    activeProjectId.value = res.project.id;
    activeProjectName.value = res.project.name;
    lastSavedAt.value = res.project.updated_at;
    await refreshList();
    return res.project;
  }

  async function importProjectJson(name: string, snapshot: CanvasSnapshot): Promise<ProjectRecord> {
    const res = await apiClient.createProject({ name, snapshot });
    activeProjectId.value = res.project.id;
    activeProjectName.value = res.project.name;
    lastSavedAt.value = res.project.updated_at;
    await refreshList();
    return res.project;
  }

  async function importProjectZip(file: File) {
    const res = await apiClient.importWorkspaceZip(file);
    if (res.mode === 'project' && res.project) {
      activeProjectId.value = res.project.id;
      activeProjectName.value = res.project.name;
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
      lastSavedAt.value = res.project.updated_at;
      const idx = projects.value.findIndex((p) => p.id === res.project.id);
      const summary: ProjectSummary = {
        schema_version: res.project.schema_version,
        id: res.project.id,
        name: res.project.name,
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
    loading,
    error,
    backendOnline,
    backendInfo,
    search,
    filter,
    sortBy,
    activeProjectId,
    activeProjectName,
    saving,
    lastSavedAt,
    filteredTemplates,
    filteredProjects,
    checkHealth,
    refreshList,
    openProject,
    createBlankProject,
    createFromTemplate,
    importProjectJson,
    importProjectZip,
    saveActive,
    renameProject,
    duplicateProject,
    deleteProject,
    closeEditor,
  };
});
