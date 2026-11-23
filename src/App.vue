<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from 'vue';
import { Film, Image as ImageIcon, Music, Play, Trash2 } from 'lucide-vue-next';
import ProjectHome from '@/components/ProjectHome.vue';
import CanvasTopbar from '@/components/CanvasTopbar.vue';
import NodeSidebar from '@/components/NodeSidebar.vue';
import CanvasViewport from '@/components/CanvasViewport.vue';
import SettingsModal from '@/components/SettingsModal.vue';
import TaskDrawer from '@/components/TaskDrawer.vue';
import { useProjectsStore } from '@/stores/projects';
import { useCanvasStore } from '@/stores/canvas';

const projects = useProjectsStore();
const canvas = useCanvasStore();

const viewportRef = ref<InstanceType<typeof CanvasViewport> | null>(null);
const showSettings = ref(false);
const showTasks = ref(false);
const viewMode = ref<'canvas' | 'list'>('canvas');

function handleKeydown(event: KeyboardEvent) {
  if (!projects.activeProjectId) return;
  const target = event.target as HTMLElement | null;
  const inInput = Boolean(target?.closest('input, textarea, select, [contenteditable="true"]'));

  if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 's') {
    event.preventDefault();
    void canvas.persistNow(false);
    return;
  }
  if (inInput) return;

  if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'z') {
    event.preventDefault();
    if (event.shiftKey) canvas.redo();
    else canvas.undo();
  } else if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'y') {
    event.preventDefault();
    canvas.redo();
  } else if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'c') {
    event.preventDefault();
    canvas.copySelected();
  } else if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'v') {
    event.preventDefault();
    canvas.pasteClipboard();
  } else if (event.key === 'Delete' || event.key === 'Backspace') {
    event.preventDefault();
    canvas.removeSelected();
  }
}

function focusNodeFromList(cardId: string) {
  canvas.selectOnly(cardId);
  viewMode.value = 'canvas';
}

onMounted(() => {
  void projects.refreshList();
  window.addEventListener('keydown', handleKeydown);
});

onBeforeUnmount(() => {
  window.removeEventListener('keydown', handleKeydown);
});
</script>

<template>
  <div class="app-root">
    <ProjectHome v-if="!projects.activeProjectId" @open-settings="showSettings = true" />

    <div v-else class="editor-shell">
      <CanvasTopbar
        v-model:view-mode="viewMode"
        @open-settings="showSettings = true"
        @toggle-tasks="showTasks = !showTasks"
      />

      <NodeSidebar
        @add-node="
          viewMode = 'canvas';
          viewportRef?.addNodeAtCenter($event);
        "
        @toggle-list="viewMode = viewMode === 'canvas' ? 'list' : 'canvas'"
        @toggle-tasks="showTasks = !showTasks"
        @open-settings="showSettings = true"
      />

      <CanvasViewport v-show="viewMode === 'canvas'" ref="viewportRef" />

      <!-- Simplified List View (matches topbar 画布 | 列表 toggle) -->
      <section v-if="viewMode === 'list'" class="list-view">
        <div class="list-inner">
          <div class="list-head">
            <h2>节点列表（{{ canvas.cards.length }}）</h2>
            <button type="button" class="btn" @click="viewMode = 'canvas'">返回画布</button>
          </div>
          <div class="list-grid">
            <article
              v-for="c in canvas.cards"
              :key="c.id"
              class="list-card"
              @click="focusNodeFromList(c.id)"
            >
              <div class="list-card-top">
                <Film v-if="c.type === 'video' || c.mediaKind === 'video'" :size="15" />
                <Music v-else-if="c.mediaKind === 'audio' || c.type === 'audio'" :size="15" />
                <ImageIcon v-else :size="15" />
                <strong>{{ c.name }}</strong>
                <span class="list-type">{{ c.type }}</span>
              </div>
              <p class="list-prompt">{{ c.prompt || (c.refs?.length ? `已连接 ${c.refs.length} 个素材` : '点击进入画布编辑') }}</p>
              <div class="list-actions" @click.stop>
                <button
                  v-if="c.type !== 'source'"
                  type="button"
                  class="btn"
                  @click="canvas.runSingleNode(c.id)"
                >
                  <Play :size="12" /> 运行
                </button>
                <button type="button" class="btn danger" @click="canvas.removeCards([c.id])">
                  <Trash2 :size="12" /> 删除
                </button>
              </div>
            </article>
          </div>
        </div>
      </section>

      <TaskDrawer v-if="showTasks" @close="showTasks = false" />
    </div>

    <SettingsModal v-if="showSettings" @close="showSettings = false" />

    <Transition name="fade">
      <div v-if="canvas.toastMessage" class="toast">{{ canvas.toastMessage }}</div>
    </Transition>
  </div>
</template>

<style scoped>
.app-root {
  height: 100%;
  width: 100%;
}

.editor-shell {
  position: relative;
  height: 100%;
  width: 100%;
  background: var(--bg);
}

.list-view {
  height: 100%;
  overflow-y: auto;
  padding: 78px 36px 40px 88px;
  background: var(--bg);
}

.list-inner {
  max-width: 1080px;
  margin: 0 auto;
}

.list-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 16px;
}

.list-head h2 {
  margin: 0;
  font-size: 16px;
}

.list-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(260px, 1fr));
  gap: 12px;
}

.list-card {
  padding: 14px;
  border-radius: 14px;
  background: var(--panel);
  border: 1px solid var(--line);
  cursor: pointer;
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.list-card:hover {
  border-color: var(--accent-border);
}

.list-card-top {
  display: flex;
  align-items: center;
  gap: 8px;
}

.list-card-top strong {
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.list-type {
  font-size: 11px;
  color: var(--muted);
  padding: 1px 7px;
  border-radius: 999px;
  background: var(--surface-group);
}

.list-prompt {
  margin: 0;
  color: var(--muted);
  font-size: 12px;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

.list-actions {
  display: flex;
  gap: 6px;
  justify-content: flex-end;
}

.fade-enter-active,
.fade-leave-active {
  transition: opacity 0.18s ease;
}

.fade-enter-from,
.fade-leave-to {
  opacity: 0;
}
</style>
