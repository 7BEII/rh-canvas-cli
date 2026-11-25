<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue';
import {
  Image as ImageIcon,
  Film,
  Music,
  FileText,
  Sparkles,
  Play,
  Pause,
  Trash2,
  Upload,
  Plus,
  Loader2,
  Link2,
  Copy,
  Check,
} from 'lucide-vue-next';
import { apiClient } from '@/api/client';
import { NODE_DEFS } from '@/data/nodeDefs';
import { useCanvasStore } from '@/stores/canvas';
import type { CanvasCard, PortKind } from '@/types/canvas';

const props = defineProps<{
  card: CanvasCard;
  connectingFrom: { cardId: string; kind: PortKind } | null;
}>();

const emit = defineEmits<{
  (e: 'drag-start', event: PointerEvent, card: CanvasCard): void;
  (e: 'start-connect', event: PointerEvent, cardId: string, kind: PortKind): void;
  (e: 'finish-connect', cardId: string, kind: PortKind): void;
}>();

const canvas = useCanvasStore();
const fileInput = ref<HTMLInputElement | null>(null);
const audioRef = ref<HTMLAudioElement | null>(null);
const genVideoRef = ref<HTMLVideoElement | null>(null);
const uploading = ref(false);
const playingAudio = ref(false);
const copiedError = ref(false);
const elapsedSec = ref(0);
let elapsedTimer: ReturnType<typeof setInterval> | null = null;

function syncElapsed() {
  const started = canvas.runStartedAt[props.card.id];
  if (started) {
    elapsedSec.value = Math.max(0, Math.floor((Date.now() - started) / 1000));
  } else {
    elapsedSec.value += 1;
  }
}

watch(
  () => [props.card.status, canvas.runStartedAt[props.card.id]] as const,
  ([st, started]) => {
    if (st === 'running') {
      elapsedSec.value = started ? Math.max(0, Math.floor((Date.now() - started) / 1000)) : 0;
      if (elapsedTimer) clearInterval(elapsedTimer);
      elapsedTimer = setInterval(syncElapsed, 1000);
    } else if (elapsedTimer) {
      clearInterval(elapsedTimer);
      elapsedTimer = null;
    }
  },
  { immediate: true },
);

onBeforeUnmount(() => {
  if (elapsedTimer) {
    clearInterval(elapsedTimer);
    elapsedTimer = null;
  }
});

const elapsedText = computed(() => {
  const m = Math.floor(elapsedSec.value / 60);
  const s = elapsedSec.value % 60;
  return `${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`;
});

const estimatedPercent = computed(() => {
  const target = props.card.type === 'video' ? 75 : 22;
  const pct = Math.round(12 + 82 * (1 - Math.exp(-elapsedSec.value / target)));
  return Math.min(94, Math.max(12, pct));
});

async function copyNodeError() {
  const raw = String(props.card.error || '').trim();
  if (!raw) return;
  let ok = false;
  if (navigator.clipboard?.writeText) {
    try {
      await navigator.clipboard.writeText(raw);
      ok = true;
    } catch {
      ok = false;
    }
  }
  if (!ok) {
    try {
      const ta = document.createElement('textarea');
      ta.value = raw;
      ta.style.position = 'fixed';
      ta.style.opacity = '0';
      document.body.appendChild(ta);
      ta.select();
      ok = document.execCommand('copy');
      document.body.removeChild(ta);
    } catch {
      ok = false;
    }
  }
  if (ok) {
    copiedError.value = true;
    canvas.showToast('已复制原始报错信息');
    window.setTimeout(() => {
      copiedError.value = false;
    }, 1800);
  } else {
    canvas.showToast('复制失败，请手动选中复制');
  }
}

const def = computed(() => NODE_DEFS[props.card.type] || NODE_DEFS.prompt);

const IMAGE_CHANNELS: Array<{ id: 'tensor' | 'ark' | 'local'; label: string }> = [
  { id: 'tensor', label: 'Tensor' },
  { id: 'ark', label: '火山方舟' },
  { id: 'local', label: '本地预览' },
];

const selectedImageChannel = computed<'tensor' | 'ark' | 'local'>(() => {
  const curId = props.card.model || def.value.defaultModel || '';
  const match = (def.value.models || []).find((m) => m.id === curId);
  if (match?.provider === 'tensor' || match?.provider === 'ark' || match?.provider === 'local') {
    return match.provider;
  }
  return 'tensor';
});

const filteredGenericModels = computed(() => {
  const all = def.value.models || [];
  if (props.card.type !== 'image') return all;
  return all.filter((m) => (m.provider || 'ark') === selectedImageChannel.value);
});

async function onImageChannelChange(ch: 'tensor' | 'ark' | 'local') {
  const list = (def.value.models || []).filter((m) => (m.provider || 'ark') === ch);
  if (list.length) {
    props.card.model = list[0].id;
    canvas.scheduleSave();
  }
  if (ch === 'tensor' || ch === 'ark') {
    try {
      await apiClient.saveConfig({ video_provider: ch });
    } catch {
      // ignore transient error
    }
  }
}

const activeIndex = computed(() => {
  const list = props.card.results || [];
  if (!list.length) return -1;
  const idx = props.card.activeResultIndex ?? list.length - 1;
  return Math.max(0, Math.min(list.length - 1, idx));
});

const activeResult = computed(() => {
  const idx = activeIndex.value;
  return idx >= 0 ? props.card.results[idx] : null;
});

const resolvedMediaKind = computed<'image' | 'video' | 'audio' | 'text'>(() => {
  if (props.card.type === 'video') return 'video';
  if (props.card.type === 'image') return 'image';
  if (props.card.type === 'audio' || props.card.type === 'ttsIndex') return 'audio';
  if (props.card.type === 'source') {
    if (activeResult.value?.kind && activeResult.value.kind !== 'text') return activeResult.value.kind;
    return props.card.mediaKind || 'image';
  }
  return 'text';
});

const isMediaCard = computed(() => props.card.type === 'source' || props.card.type === 'video');

const primaryOutputKind = computed<PortKind>(() => {
  if (props.card.type === 'source') return resolvedMediaKind.value === 'text' ? 'image' : resolvedMediaKind.value;
  return def.value.outputs[0]?.kind || 'image';
});

const acceptsConnection = computed(() => {
  if (!props.connectingFrom) return false;
  if (props.connectingFrom.cardId === props.card.id) return false;
  return def.value.inputs.some((p) => p.kind === props.connectingFrom!.kind);
});

const waveformBars = [18, 34, 24, 52, 72, 46, 86, 62, 38, 78, 92, 54, 42, 68, 84, 50, 30, 62, 74, 40, 26, 48, 32, 20];

const cardWidth = computed(() => {
  if (props.card.type === 'image') return Math.max(props.card.width || 0, 290);
  if (props.card.width) return props.card.width;
  if (props.card.type === 'video') return 210;
  if (props.card.type === 'source') return 176;
  return def.value.defaultWidth || 260;
});

const cardHeight = computed(() => {
  if (props.card.height) return props.card.height;
  if (props.card.type === 'video') return 280;
  if (props.card.type === 'source') {
    return resolvedMediaKind.value === 'audio' ? 112 : 210;
  }
  return undefined;
});

const mediaSrc = computed(() => activeResult.value?.url || activeResult.value?.dataUrl || '');

async function handleFileUpload(event: Event) {
  const input = event.target as HTMLInputElement;
  const file = input.files?.[0];
  input.value = '';
  if (!file) return;
  uploading.value = true;
  try {
    const res = await apiClient.uploadAsset(file);
    playingAudio.value = false;
    props.card.mediaKind = res.asset.kind;
    props.card.results = [
      {
        kind: res.asset.kind,
        label: res.asset.name,
        name: res.asset.name,
        url: res.asset.url,
        path: res.asset.path,
      },
    ];
    props.card.activeResultIndex = 0;
    props.card.status = 'done';
    canvas.markDownstreamStale(props.card.id, '参考素材已更新');
    canvas.scheduleSave();
    const adjusted = Boolean((res.asset as any)?.duration_adjusted);
    canvas.showToast(
      adjusted ? `已替换素材并自动适配时长（2-15s）：${res.asset.name}` : `已替换素材：${res.asset.name}`,
    );
  } catch (err: any) {
    canvas.showToast(`上传失败：${err?.message || '未知错误'}`);
  } finally {
    uploading.value = false;
  }
}

function toggleAudioPlay() {
  if (!audioRef.value || !mediaSrc.value) return;
  if (playingAudio.value) {
    audioRef.value.pause();
    playingAudio.value = false;
  } else {
    void audioRef.value.play();
    playingAudio.value = true;
  }
}

function editArkAssetId() {
  const current = props.card.arkAssetId || '';
  const next = window.prompt(
    '绑定火山方舟私域资产 ID（用于真人图免拦截，如 asset-2026xxxx 或 asset://asset-2026xxxx；留空则取消绑定）：',
    current,
  );
  if (next === null) return;
  const trimmed = next.trim();
  props.card.arkAssetId = trimmed || undefined;
  canvas.scheduleSave();
  canvas.showToast(trimmed ? `已绑定火山资产：${trimmed}` : '已取消火山资产绑定');
}

function onInputDrop() {
  if (!props.connectingFrom) return;
  emit('finish-connect', props.card.id, props.connectingFrom.kind);
}

let shieldDownPos: { x: number; y: number } | null = null;
let shieldDragged = false;

function onVideoShieldPointerDown(event: PointerEvent) {
  if (event.button !== 0) return;
  event.stopPropagation();
  event.preventDefault();
  shieldDownPos = { x: event.clientX, y: event.clientY };
  shieldDragged = false;

  const onMove = (e: PointerEvent) => {
    if (!shieldDownPos) return;
    if (Math.abs(e.clientX - shieldDownPos.x) > 3 || Math.abs(e.clientY - shieldDownPos.y) > 3) {
      shieldDragged = true;
    }
  };
  const onUp = () => {
    window.removeEventListener('pointermove', onMove);
    window.removeEventListener('pointerup', onUp, true);
    window.removeEventListener('pointercancel', onUp, true);
  };
  window.addEventListener('pointermove', onMove);
  window.addEventListener('pointerup', onUp, true);
  window.addEventListener('pointercancel', onUp, true);

  emit('drag-start', event, props.card);
}

function onVideoShieldClick(event: MouseEvent) {
  event.stopPropagation();
  if (shieldDragged) {
    shieldDragged = false;
    return;
  }
  const video = genVideoRef.value;
  if (!video) return;
  if (video.paused) {
    void video.play();
  } else {
    video.pause();
  }
}

function onVideoControlsPointerDown(event: PointerEvent) {
  event.stopPropagation();
  if (!props.card.selected) canvas.selectOnly(props.card.id);
}
</script>

<template>
  <article
    class="rh-node-wrap"
    :class="[
      card.status || 'idle',
      {
        selected: card.selected,
        'media-card': isMediaCard,
        'drop-ready': acceptsConnection,
      },
    ]"
    :style="{
      left: card.x + 'px',
      top: card.y + 'px',
      width: cardWidth + 'px',
    }"
    @pointerdown.stop="
      $event.ctrlKey || $event.metaKey || $event.shiftKey
        ? canvas.toggleSelect(card.id)
        : !card.selected && canvas.selectOnly(card.id)
    "
    @pointerup="acceptsConnection && onInputDrop()"
  >
    <!-- External Label Row Above Card (RHTV style) -->
    <div class="rh-label-row" @pointerdown.stop="emit('drag-start', $event, card)">
      <span class="rh-label-icon">
        <ImageIcon v-if="resolvedMediaKind === 'image'" :size="13" />
        <Film v-else-if="resolvedMediaKind === 'video'" :size="13" />
        <Music v-else-if="resolvedMediaKind === 'audio'" :size="13" />
        <FileText v-else :size="13" />
      </span>
      <input
        v-model="card.name"
        class="rh-label-input"
        @pointerdown.stop
        @change="canvas.scheduleSave()"
      />
      <div class="rh-quick-actions" @pointerdown.stop>
        <button
          v-if="card.type === 'source'"
          type="button"
          class="mini-act"
          :class="{ 'has-asset': Boolean(card.arkAssetId) }"
          :title="card.arkAssetId ? `已绑定火山资产：${card.arkAssetId}` : '绑定火山方舟资产 ID (asset://)'"
          @click="editArkAssetId"
        >
          <Link2 :size="12" />
        </button>
        <button
          v-if="card.type === 'source'"
          type="button"
          class="mini-act"
          title="替换素材"
          @click="fileInput?.click()"
        >
          <Upload :size="12" />
        </button>
        <button
          v-if="card.type !== 'source'"
          type="button"
          class="mini-act"
          title="运行此节点"
          :disabled="card.status === 'running'"
          @click="canvas.runSingleNode(card.id)"
        >
          <Play :size="12" />
        </button>
        <button
          type="button"
          class="mini-act del"
          title="删除节点"
          @click="canvas.removeCards([card.id])"
        >
          <Trash2 :size="12" />
        </button>
      </div>
    </div>

    <!-- Left Input Handle (single unified drop handle for clean RHTV curves) -->
    <button
      v-if="def.inputs.length"
      type="button"
      class="rh-handle rh-handle-in"
      :class="{ ready: acceptsConnection }"
      title="连接上游素材到此节点"
      @pointerup="onInputDrop"
    />

    <!-- Right Output Handle (+ circular connector) -->
    <button
      v-if="def.outputs.length"
      type="button"
      class="rh-handle rh-handle-out"
      :class="{ connecting: connectingFrom?.cardId === card.id }"
      title="拖拽连线到视频生成节点"
      @pointerdown.stop.prevent="emit('start-connect', $event, card.id, primaryOutputKind)"
    >
      <Plus :size="11" />
    </button>

    <!-- 1. Borderless Media Source Card -->
    <div
      v-if="card.type === 'source'"
      class="rh-surface media-surface"
      :style="{ height: (cardHeight || 200) + 'px' }"
      @pointerdown.stop="emit('drag-start', $event, card)"
    >
      <input
        ref="fileInput"
        type="file"
        :accept="
          resolvedMediaKind === 'video'
            ? 'video/*,.mp4,.mov,.webm,.mkv,.m4v,.avi'
            : resolvedMediaKind === 'audio'
              ? 'audio/*,.mp3,.wav,.m4a,.aac,.ogg,.flac,.opus'
              : 'image/*,video/*,audio/*,.mp4,.mov,.webm,.mkv,.m4v,.avi,.mp3,.wav,.m4a,.aac,.ogg,.flac,.opus'
        "
        hidden
        @change="handleFileUpload"
      />

      <!-- Audio Waveform Card -->
      <div v-if="resolvedMediaKind === 'audio'" class="audio-wave-card">
        <div class="wave-bars">
          <span
            v-for="(h, i) in waveformBars"
            :key="i"
            class="wave-bar"
            :class="{ active: playingAudio && i % 3 === 0 }"
            :style="{ height: h + '%' }"
          />
        </div>
        <div class="audio-foot" @pointerdown.stop>
          <button
            v-if="mediaSrc"
            type="button"
            class="audio-play-btn"
            @click="toggleAudioPlay"
          >
            <Pause v-if="playingAudio" :size="12" />
            <Play v-else :size="12" />
          </button>
          <button v-else type="button" class="audio-upload-link" @click="fileInput?.click()">
            <Upload :size="11" /> 上传
          </button>
          <span class="audio-time">{{ activeResult?.label || '00:15' }}</span>
        </div>
        <audio
          v-if="mediaSrc"
          ref="audioRef"
          :src="mediaSrc"
          @ended="playingAudio = false"
        />
      </div>

      <!-- Video Reference Card -->
      <div v-else-if="resolvedMediaKind === 'video'" class="video-ref-card">
        <video
          v-if="mediaSrc && !mediaSrc.startsWith('data:image/svg')"
          :src="mediaSrc"
          muted
          autoplay
          loop
          playsinline
          draggable="false"
          class="cover-media"
        />
        <img v-else-if="mediaSrc" :src="mediaSrc" :alt="card.name" class="cover-media" />
        <div v-else class="empty-media" @click.stop="fileInput?.click()">
          <Film :size="22" />
          <span>{{ uploading ? '上传中…' : '点击上传参考视频' }}</span>
        </div>
        <div v-if="mediaSrc" class="play-overlay">
          <Play :size="16" fill="currentColor" />
        </div>
      </div>

      <!-- Image Reference Card -->
      <div v-else class="image-ref-card">
        <img v-if="mediaSrc" :src="mediaSrc" :alt="card.name" class="cover-media" draggable="false" />
        <div v-else class="empty-media" @click.stop="fileInput?.click()">
          <ImageIcon :size="22" />
          <span>{{ uploading ? '上传中…' : '点击上传图片' }}</span>
        </div>
        <span v-if="card.arkAssetId" class="asset-id-badge" :title="card.arkAssetId">asset://</span>
      </div>
    </div>

    <!-- 2. Video Generation Node Card (RHTV dark tall card; detailed editing in bottom VideoComposerPanel) -->
    <div
      v-else-if="card.type === 'video'"
      class="rh-surface video-gen-surface"
      :style="{ height: (cardHeight || 280) + 'px' }"
      @pointerdown.stop="emit('drag-start', $event, card)"
    >
      <template v-if="card.status === 'running'">
        <div class="video-placeholder running-state">
          <Loader2 :size="26" class="spin lime" />
          <strong class="run-title">云端视频生成中… {{ estimatedPercent }}%</strong>
          <div class="run-bar-track">
            <div class="run-bar-progress" :style="{ width: estimatedPercent + '%' }" />
          </div>
          <span class="run-sub">已耗时 {{ elapsedText }} · 正在轮询云端成片</span>
          <button type="button" class="node-stop-btn" @pointerdown.stop @click="canvas.cancelNode(card.id)">停止生成</button>
        </div>
      </template>

      <template v-else-if="activeResult && (activeResult.url || activeResult.dataUrl)">
        <template
          v-if="activeResult.kind === 'video' && activeResult.url && /\.(mp4|mov|webm|mkv)(\?|$)/i.test(activeResult.url)"
        >
          <video
            ref="genVideoRef"
            :src="activeResult.url"
            controls
            autoplay
            loop
            muted
            playsinline
            draggable="false"
            class="cover-media"
            @pointerdown="onVideoControlsPointerDown"
          />
          <div
            class="video-drag-shield"
            @pointerdown="onVideoShieldPointerDown"
            @click="onVideoShieldClick"
          />
        </template>
        <img
          v-else
          :src="activeResult.url || activeResult.dataUrl"
          :alt="card.name"
          class="cover-media"
          draggable="false"
        />
        <div class="gen-badge">
          {{ card.resolution || '720p' }} · {{ card.duration || 15 }}s
        </div>
      </template>

      <template v-else>
        <div class="video-placeholder">
          <div class="placeholder-icon">
            <Film :size="20" />
          </div>
          <span>请选中节点后在下方输入提示词生成</span>
        </div>
      </template>

      <div v-if="card.error" class="node-err-pill" @pointerdown.stop>
        <div class="err-head">
          <span>报错信息</span>
          <button
            type="button"
            class="err-copy-btn"
            :class="{ copied: copiedError }"
            title="复制原始报错"
            @click.stop="copyNodeError"
          >
            <Check v-if="copiedError" :size="11" />
            <Copy v-else :size="11" />
            <span>{{ copiedError ? '已复制' : 'Copy' }}</span>
          </button>
        </div>
        <div class="err-text">{{ card.error }}</div>
      </div>
    </div>

    <!-- 3. Compact Generic Card (Image Gen / Prompt / Legacy nodes) -->
    <div v-else class="rh-surface generic-surface" @pointerdown.stop="emit('drag-start', $event, card)">
      <div v-if="card.status === 'running'" class="generic-running-overlay" @pointerdown.stop>
        <Loader2 :size="24" class="spin lime" />
        <strong class="run-title">{{ card.type === 'image' ? 'AI 生图生成中…' : '节点运行中…' }} {{ estimatedPercent }}%</strong>
        <div class="run-bar-track">
          <div class="run-bar-progress" :style="{ width: estimatedPercent + '%' }" />
        </div>
        <span class="run-sub">已耗时 {{ elapsedText }}</span>
        <button type="button" class="node-stop-btn" @click="canvas.cancelNode(card.id)">停止生成</button>
      </div>
      <div v-if="activeResult && (activeResult.url || activeResult.dataUrl)" class="generic-preview">
        <img :src="activeResult.url || activeResult.dataUrl" :alt="card.name" class="cover-media" draggable="false" />
      </div>
      <div class="generic-body">
        <div v-if="def.models && def.models.length" class="generic-controls-wrap" @pointerdown.stop>
          <div v-if="card.type === 'image'" class="generic-controls">
            <select
              :value="selectedImageChannel"
              class="generic-select channel"
              title="选择渠道"
              @change="onImageChannelChange(($event.target as HTMLSelectElement).value as 'tensor' | 'ark' | 'local')"
            >
              <option v-for="ch in IMAGE_CHANNELS" :key="ch.id" :value="ch.id">{{ ch.label }}</option>
            </select>
            <select
              :value="card.aspect || '16:9'"
              class="generic-select aspect"
              title="画面比例"
              @change="
                card.aspect = ($event.target as HTMLSelectElement).value;
                canvas.scheduleSave();
              "
            >
              <option value="1:1">1:1</option>
              <option value="16:9">16:9</option>
              <option value="9:16">9:16</option>
              <option value="3:4">3:4</option>
              <option value="4:3">4:3</option>
              <option value="2:3">2:3</option>
              <option value="3:2">3:2</option>
            </select>
          </div>
          <div class="generic-controls">
            <select
              :value="card.model || filteredGenericModels[0]?.id"
              class="generic-select"
              title="选择生成模型"
              @change="
                card.model = ($event.target as HTMLSelectElement).value;
                canvas.scheduleSave();
              "
            >
              <option v-for="m in filteredGenericModels" :key="m.id" :value="m.id">{{ m.label }}</option>
            </select>
          </div>
        </div>
        <textarea
          v-if="card.type === 'prompt' || card.type === 'image' || card.type === 'storyboard'"
          v-model="card.prompt"
          class="generic-textarea"
          rows="3"
          placeholder="输入提示词…"
          @pointerdown.stop
          @change="canvas.scheduleSave()"
        />
        <div v-if="card.error" class="generic-err" @pointerdown.stop>
          <div class="err-head">
            <span>报错信息</span>
            <button
              type="button"
              class="err-copy-btn"
              :class="{ copied: copiedError }"
              title="复制原始报错"
              @click.stop="copyNodeError"
            >
              <Check v-if="copiedError" :size="11" />
              <Copy v-else :size="11" />
              <span>{{ copiedError ? '已复制' : 'Copy' }}</span>
            </button>
          </div>
          <div class="err-text">{{ card.error }}</div>
        </div>
        <div class="generic-foot">
          <span class="chip-tag">
            <Sparkles :size="11" /> {{ def.title }}
          </span>
          <button
            type="button"
            class="mini-run-btn"
            :disabled="card.status === 'running'"
            @pointerdown.stop
            @click="canvas.runSingleNode(card.id)"
          >
            {{ card.status === 'running' ? '运行中…' : '运行' }}
          </button>
        </div>
      </div>
    </div>
  </article>
</template>

<style scoped>
.rh-node-wrap {
  position: absolute;
  user-select: none;
}

.rh-label-row {
  position: absolute;
  left: 2px;
  right: 2px;
  bottom: calc(100% + 6px);
  display: flex;
  align-items: center;
  gap: 5px;
  cursor: grab;
}

.rh-label-row:active {
  cursor: grabbing;
}

.rh-label-icon {
  color: var(--muted);
  display: grid;
  place-items: center;
  flex-shrink: 0;
}

.rh-label-input {
  flex: 1;
  min-width: 0;
  border: 0;
  background: transparent;
  color: rgba(255, 255, 255, 0.78);
  font-size: 12px;
  font-weight: 500;
  outline: none;
  padding: 1px 4px;
  border-radius: 5px;
  overflow: hidden;
  text-overflow: ellipsis;
}

.rh-label-input:focus {
  background: rgba(255, 255, 255, 0.08);
  color: #ffffff;
}

.rh-quick-actions {
  display: flex;
  align-items: center;
  gap: 2px;
  opacity: 0;
  transition: opacity 0.14s ease;
}

.rh-node-wrap:hover .rh-quick-actions,
.rh-node-wrap.selected .rh-quick-actions {
  opacity: 1;
}

.mini-act {
  width: 22px;
  height: 22px;
  border-radius: 6px;
  border: 0;
  background: rgba(28, 28, 30, 0.9);
  color: var(--text-secondary);
  display: grid;
  place-items: center;
}

.mini-act:hover {
  color: #ffffff;
  background: var(--surface-selected);
}

.mini-act.del:hover {
  color: var(--danger);
}

.mini-act.has-asset {
  color: var(--accent);
}

.asset-id-badge {
  position: absolute;
  left: 6px;
  bottom: 6px;
  padding: 1px 6px;
  border-radius: 5px;
  background: rgba(15, 15, 17, 0.85);
  border: 1px solid var(--accent-border);
  color: var(--accent);
  font-size: 10px;
  font-weight: 600;
  pointer-events: none;
}

.rh-surface {
  position: relative;
  border-radius: 12px;
  background: var(--panel);
  border: 1px solid rgba(255, 255, 255, 0.09);
  overflow: hidden;
  transition: box-shadow 0.16s ease, border-color 0.16s ease;
}

.rh-node-wrap:hover .rh-surface {
  box-shadow: 0 0 0 1.5px rgba(204, 255, 0, 0.58), 0 0 10px rgba(204, 255, 0, 0.25);
}

.rh-node-wrap.selected .rh-surface {
  border-color: #9e9e9e;
  box-shadow: 0 0 0 1.5px rgba(255, 255, 255, 0.5), 0 12px 32px rgba(0, 0, 0, 0.55);
}

.rh-node-wrap.drop-ready .rh-surface {
  border-color: var(--accent);
  box-shadow: 0 0 0 2px rgba(204, 255, 0, 0.7), 0 0 16px rgba(204, 255, 0, 0.3);
}

.rh-handle {
  position: absolute;
  top: 50%;
  transform: translateY(-50%);
  z-index: 8;
  border-radius: 50%;
  display: grid;
  place-items: center;
  transition: transform 0.14s ease, opacity 0.14s ease, border-color 0.14s ease, background 0.14s ease;
}

.rh-handle-in {
  left: -7px;
  width: 13px;
  height: 13px;
  border: 1.5px solid rgba(255, 255, 255, 0.55);
  background: #171717;
  cursor: crosshair;
}

.rh-handle-in.ready,
.rh-handle-in:hover {
  border-color: var(--accent);
  background: var(--accent);
  transform: translateY(-50%) scale(1.25);
}

.rh-handle-out {
  right: -13px;
  width: 22px;
  height: 22px;
  border: 1px solid rgba(255, 255, 255, 0.26);
  background: rgba(29, 29, 31, 0.96);
  color: rgba(255, 255, 255, 0.85);
  cursor: crosshair;
  opacity: 0.75;
}

.rh-node-wrap:hover .rh-handle-out,
.rh-node-wrap.selected .rh-handle-out,
.rh-handle-out.connecting {
  opacity: 1;
  border-color: rgba(255, 255, 255, 0.6);
}

.rh-handle-out:hover,
.rh-handle-out.connecting {
  background: var(--accent);
  border-color: var(--accent);
  color: #11130a;
  transform: translateY(-50%) scale(1.12);
}

.image-ref-card,
.video-ref-card {
  width: 100%;
  height: 100%;
  position: relative;
  cursor: grab;
}

.cover-media {
  width: 100%;
  height: 100%;
  object-fit: cover;
  display: block;
}

.empty-media {
  width: 100%;
  height: 100%;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 8px;
  color: var(--muted);
  font-size: 12px;
  cursor: pointer;
  background: #161618;
}

.empty-media:hover {
  color: var(--text);
  background: #1c1c20;
}

.play-overlay {
  position: absolute;
  left: 50%;
  top: 50%;
  transform: translate(-50%, -50%);
  width: 34px;
  height: 34px;
  border-radius: 50%;
  background: rgba(0, 0, 0, 0.56);
  border: 1px solid rgba(255, 255, 255, 0.3);
  color: #ffffff;
  display: grid;
  place-items: center;
  pointer-events: none;
}

.audio-wave-card {
  width: 100%;
  height: 100%;
  background: #151517;
  padding: 14px 12px 10px;
  display: flex;
  flex-direction: column;
  justify-content: space-between;
  cursor: grab;
}

.wave-bars {
  flex: 1;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 3px;
  padding: 4px 2px;
}

.wave-bar {
  flex: 1;
  min-height: 4px;
  border-radius: 999px;
  background: #525252;
  transition: background 0.16s ease;
}

.wave-bar.active {
  background: #ffffff;
}

.audio-foot {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 6px;
  padding-top: 6px;
}

.audio-play-btn {
  width: 24px;
  height: 24px;
  border-radius: 50%;
  border: 0;
  background: rgba(255, 255, 255, 0.12);
  color: #ffffff;
  display: grid;
  place-items: center;
}

.audio-upload-link {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  border: 0;
  background: rgba(255, 255, 255, 0.08);
  color: rgba(255, 255, 255, 0.8);
  border-radius: 999px;
  padding: 2px 8px;
  font-size: 11px;
  white-space: nowrap;
  flex-shrink: 0;
}

.audio-time {
  font-size: 11px;
  color: var(--muted);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.video-gen-surface {
  background: #151517;
  cursor: grab;
}

.video-drag-shield {
  position: absolute;
  top: 0;
  left: 0;
  right: 0;
  bottom: 68px;
  z-index: 2;
  cursor: grab;
}

.video-drag-shield:active {
  cursor: grabbing;
}

.video-placeholder {
  width: 100%;
  height: 100%;
  padding: 18px;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  text-align: center;
  gap: 10px;
  color: var(--muted);
  font-size: 12px;
}

.placeholder-icon {
  width: 40px;
  height: 40px;
  border-radius: 12px;
  border: 1px solid var(--line);
  background: rgba(255, 255, 255, 0.04);
  display: grid;
  place-items: center;
  color: rgba(255, 255, 255, 0.5);
}

.gen-badge {
  position: absolute;
  left: 8px;
  bottom: 8px;
  padding: 2px 8px;
  border-radius: 999px;
  background: rgba(15, 15, 17, 0.78);
  border: 1px solid rgba(255, 255, 255, 0.14);
  font-size: 10.5px;
  color: #ffffff;
}

.node-err-pill {
  position: absolute;
  left: 8px;
  right: 8px;
  bottom: 8px;
  z-index: 5;
  max-height: 170px;
  overflow-y: auto;
  padding: 6px 8px;
  border-radius: 8px;
  background: rgba(42, 16, 18, 0.94);
  backdrop-filter: blur(8px);
  border: 1px solid rgba(255, 107, 107, 0.5);
  color: #ffd2d2;
  font-size: 11px;
  display: grid;
  gap: 4px;
  cursor: default;
}

.err-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 6px;
  font-size: 10.5px;
  color: #ff9c9c;
}

.err-copy-btn {
  display: inline-flex;
  align-items: center;
  gap: 3px;
  padding: 1px 6px;
  border-radius: 5px;
  border: 1px solid rgba(255, 160, 160, 0.35);
  background: rgba(255, 255, 255, 0.08);
  color: #ffe3e3;
  font-size: 10.5px;
  line-height: 1.3;
  cursor: pointer;
}

.err-copy-btn:hover {
  background: rgba(255, 255, 255, 0.18);
  color: #ffffff;
}

.err-copy-btn.copied {
  border-color: rgba(204, 255, 0, 0.6);
  background: rgba(204, 255, 0, 0.16);
  color: var(--accent);
}

.err-text {
  line-height: 1.4;
  word-break: break-word;
  user-select: text;
  cursor: text;
}

.generic-surface {
  position: relative;
  width: 100%;
  box-sizing: border-box;
}

.generic-running-overlay {
  position: absolute;
  inset: 0;
  z-index: 6;
  background: rgba(15, 16, 20, 0.88);
  backdrop-filter: blur(8px);
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 8px;
  padding: 16px;
  text-align: center;
}

.run-bar-progress {
  position: relative;
  height: 100%;
  border-radius: 999px;
  background: linear-gradient(90deg, #9be600, #ccff00, #e8ff85);
  transition: width 0.85s ease-out;
  overflow: hidden;
}

.run-bar-progress::after {
  content: '';
  position: absolute;
  inset: 0;
  background: linear-gradient(90deg, transparent, rgba(255, 255, 255, 0.45), transparent);
  animation: bar-shimmer 1.4s ease-in-out infinite;
}

@keyframes bar-shimmer {
  0% {
    transform: translateX(-100%);
  }
  100% {
    transform: translateX(100%);
  }
}

.generic-preview {
  width: 100%;
  height: 168px;
  background: #0d0d0f;
  display: flex;
  align-items: center;
  justify-content: center;
  overflow: hidden;
}

.generic-preview .cover-media {
  width: 100%;
  height: 100%;
  object-fit: contain;
}

.generic-body {
  width: 100%;
  box-sizing: border-box;
  padding: 10px;
  display: flex;
  flex-direction: column;
  gap: 8px;
  min-width: 0;
}

.generic-textarea {
  width: 100%;
  box-sizing: border-box;
  min-width: 0;
  border: 1px solid var(--line);
  border-radius: 8px;
  background: var(--surface-group);
  color: var(--text);
  padding: 7px 9px;
  font-size: 12px;
  line-height: 1.45;
  resize: vertical;
  outline: none;
  user-select: text;
}

.generic-foot {
  width: 100%;
  box-sizing: border-box;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}

.generic-controls-wrap {
  width: 100%;
  box-sizing: border-box;
  display: flex;
  flex-direction: column;
  gap: 6px;
  min-width: 0;
}

.generic-controls {
  width: 100%;
  box-sizing: border-box;
  display: flex;
  align-items: center;
  gap: 6px;
  min-width: 0;
}

.generic-select {
  flex: 1 1 0;
  width: 0;
  min-width: 0;
  max-width: 100%;
  box-sizing: border-box;
  border: 1px solid var(--line);
  border-radius: 7px;
  padding: 5px 7px;
  background: var(--panel);
  color: var(--text);
  font-size: 11.5px;
  outline: none;
  overflow: hidden;
  text-overflow: ellipsis;
}

.generic-select.aspect {
  flex: 0 0 68px;
  width: 68px;
}

.generic-select:focus {
  border-color: var(--accent-border);
}

.generic-err {
  width: 100%;
  box-sizing: border-box;
  font-size: 11px;
  line-height: 1.4;
  color: #ffb8b8;
  background: rgba(255, 107, 107, 0.14);
  border: 1px solid rgba(255, 107, 107, 0.35);
  border-radius: 7px;
  padding: 5px 7px;
  word-break: break-word;
}

.chip-tag {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-size: 11px;
  color: var(--muted);
}

.mini-run-btn {
  border: 0;
  border-radius: 999px;
  padding: 4px 10px;
  background: var(--action-surface);
  color: var(--action-ink);
  font-size: 11.5px;
  font-weight: 600;
}

.lime {
  color: var(--accent);
}

.spin {
  animation: spin 1s linear infinite;
}

.running-state {
  gap: 8px;
}

.run-title {
  font-size: 13px;
  color: #ffffff;
}

.run-sub {
  font-size: 11px;
  color: var(--muted);
  line-height: 1.4;
}

.node-stop-btn {
  margin-top: 4px;
  border: 1px solid rgba(255, 107, 107, 0.5);
  background: rgba(255, 107, 107, 0.16);
  color: #ffc2c2;
  border-radius: 999px;
  padding: 4px 12px;
  font-size: 11.5px;
}

.node-stop-btn:hover {
  background: rgba(255, 107, 107, 0.32);
  color: #ffffff;
}

.run-bar-track {
  width: 82%;
  height: 5px;
  border-radius: 999px;
  background: rgba(255, 255, 255, 0.12);
  overflow: hidden;
}

.run-bar-fill {
  width: 42%;
  height: 100%;
  border-radius: 999px;
  background: var(--accent);
  animation: indeterminate 1.4s ease-in-out infinite;
}

@keyframes indeterminate {
  0% {
    transform: translateX(-100%);
  }
  100% {
    transform: translateX(260%);
  }
}

@keyframes spin {
  to {
    transform: rotate(360deg);
  }
}
</style>
