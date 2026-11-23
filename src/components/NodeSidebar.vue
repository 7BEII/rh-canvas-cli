<script setup lang="ts">
import { ref } from 'vue';
import {
  Plus,
  Image as ImageIcon,
  Film,
  Music,
  Sparkles,
  FileText,
  Upload,
  Layers,
  Settings,
} from 'lucide-vue-next';
import { apiClient } from '@/api/client';
import { useCanvasStore } from '@/stores/canvas';
import type { CanvasCard, NodeType } from '@/types/canvas';

const emit = defineEmits<{
  (e: 'add-node', payload: { type: NodeType; extra?: Partial<CanvasCard> }): void;
  (e: 'toggle-list'): void;
  (e: 'toggle-tasks'): void;
  (e: 'open-settings'): void;
}>();

const canvas = useCanvasStore();
const menuOpen = ref(false);
const uploadInput = ref<HTMLInputElement | null>(null);
const uploading = ref(false);

function pickNode(type: NodeType, extra?: Partial<CanvasCard>) {
  menuOpen.value = false;
  emit('add-node', { type, extra });
}

function triggerUpload() {
  menuOpen.value = false;
  uploadInput.value?.click();
}

async function handleQuickUpload(event: Event) {
  const input = event.target as HTMLInputElement;
  const files = Array.from(input.files || []);
  input.value = '';
  if (!files.length) return;
  uploading.value = true;
  try {
    for (const file of files) {
      const res = await apiClient.uploadAsset(file);
      const kind = res.asset.kind;
      emit('add-node', {
        type: 'source',
        extra: {
          mediaKind: kind,
          name: res.asset.name.replace(/\.[^.]+$/, ''),
          width: kind === 'audio' ? 180 : 180,
          height: kind === 'audio' ? 112 : 210,
          results: [
            {
              kind,
              label: res.asset.name,
              name: res.asset.name,
              url: res.asset.url,
              path: res.asset.path,
            },
          ],
          activeResultIndex: 0,
          status: 'done',
        },
      });
    }
    canvas.showToast(`已添加 ${files.length} 个本地素材到画布`);
  } catch (err: any) {
    canvas.showToast(`上传失败：${err?.message || '未知错误'}`);
  } finally {
    uploading.value = false;
  }
}
</script>

<template>
  <aside class="floating-dock" @pointerdown.stop>
    <button
      type="button"
      class="add-circle-btn"
      :class="{ open: menuOpen }"
      title="添加节点 / 上传素材"
      @click="menuOpen = !menuOpen"
    >
      <Plus :size="20" :stroke-width="2.4" />
    </button>

    <button type="button" class="dock-btn" title="快速上传图片/视频/音频" @click="triggerUpload">
      <Upload :size="17" />
    </button>

    <button type="button" class="dock-btn" title="节点列表视图" @click="emit('toggle-list')">
      <Layers :size="17" />
    </button>

    <button type="button" class="dock-btn" title="全局设置" @click="emit('open-settings')">
      <Settings :size="17" />
    </button>

    <input
      ref="uploadInput"
      type="file"
      accept="image/*,video/*,audio/*"
      multiple
      hidden
      @change="handleQuickUpload"
    />

    <!-- RHTV Add Node Popover -->
    <Transition name="pop">
      <div v-if="menuOpen" class="add-popover">
        <div class="pop-section-title">添加节点</div>
        <button
          type="button"
          class="pop-item featured"
          @click="pickNode('video', { name: 'Seedance 2.0 Mini', videoMode: 'omni', width: 210, height: 280 })"
        >
          <Film :size="15" class="lime-icon" />
          <span>视频生成（全能参考）</span>
        </button>
        <button
          type="button"
          class="pop-item"
          @click="pickNode('source', { mediaKind: 'image', name: '图片素材', width: 176, height: 220 })"
        >
          <ImageIcon :size="15" />
          <span>图片素材</span>
        </button>
        <button
          type="button"
          class="pop-item"
          @click="pickNode('source', { mediaKind: 'video', name: '动作参考视频', width: 176, height: 118 })"
        >
          <Film :size="15" />
          <span>视频素材</span>
        </button>
        <button
          type="button"
          class="pop-item"
          @click="pickNode('source', { mediaKind: 'audio', name: '背景音乐', width: 176, height: 112 })"
        >
          <Music :size="15" />
          <span>音频素材</span>
        </button>
        <button
          type="button"
          class="pop-item"
          @click="pickNode('image', { name: 'AI 生图', width: 240 })"
        >
          <Sparkles :size="15" />
          <span>AI 生图</span>
        </button>
        <button
          type="button"
          class="pop-item"
          @click="pickNode('prompt', { name: '文本提示词', width: 240 })"
        >
          <FileText :size="15" />
          <span>文本</span>
        </button>

        <div class="pop-divider" />
        <div class="pop-section-title">添加资源</div>
        <button type="button" class="pop-item" :disabled="uploading" @click="triggerUpload">
          <Upload :size="15" />
          <span>{{ uploading ? '上传中…' : '本地上传（支持多选）' }}</span>
        </button>
      </div>
    </Transition>
  </aside>
</template>

<style scoped>
.floating-dock {
  position: fixed;
  left: 16px;
  top: 50%;
  transform: translateY(-50%);
  width: 48px;
  padding: 8px 5px 12px;
  border-radius: 24px;
  background: var(--toolbar-surface);
  border: 1px solid var(--line);
  box-shadow: var(--toolbar-shadow);
  backdrop-filter: blur(20px) saturate(118%);
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 8px;
  z-index: 50;
}

.add-circle-btn {
  width: 36px;
  height: 36px;
  border-radius: 50%;
  border: 0;
  background: var(--action-surface);
  color: var(--action-ink);
  display: grid;
  place-items: center;
  margin-bottom: 6px;
  transition: transform 0.18s ease, background 0.15s ease;
}

.add-circle-btn:hover {
  background: #e8e8e8;
}

.add-circle-btn.open {
  transform: rotate(45deg);
  background: var(--accent);
}

.dock-btn {
  width: 36px;
  height: 36px;
  border-radius: 12px;
  border: 0;
  background: transparent;
  color: var(--text-secondary);
  display: grid;
  place-items: center;
  transition: background 0.14s ease, color 0.14s ease;
}

.dock-btn:hover {
  background: rgba(255, 255, 255, 0.08);
  color: var(--text);
}

.add-popover {
  position: absolute;
  left: 60px;
  top: 0;
  width: 214px;
  padding: 8px;
  border-radius: 16px;
  background: rgba(25, 25, 27, 0.98);
  border: 1px solid var(--line);
  box-shadow: var(--shadow);
  backdrop-filter: blur(20px);
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.pop-section-title {
  padding: 5px 10px 4px;
  font-size: 11px;
  color: var(--muted);
}

.pop-item {
  display: flex;
  align-items: center;
  gap: 10px;
  width: 100%;
  padding: 8px 10px;
  border: 0;
  border-radius: 10px;
  background: transparent;
  color: rgba(255, 255, 255, 0.88);
  font-size: 13px;
  text-align: left;
  transition: background 0.12s ease;
}

.pop-item:hover {
  background: rgba(255, 255, 255, 0.08);
}

.pop-item.featured {
  background: rgba(204, 255, 0, 0.08);
  color: #ffffff;
}

.pop-item.featured:hover {
  background: rgba(204, 255, 0, 0.16);
}

.lime-icon {
  color: var(--accent);
}

.pop-divider {
  height: 1px;
  background: var(--line);
  margin: 5px 6px;
}

.pop-enter-active,
.pop-leave-active {
  transition: opacity 0.14s ease, transform 0.14s ease;
}

.pop-enter-from,
.pop-leave-to {
  opacity: 0;
  transform: translateX(-6px) scale(0.98);
}
</style>
