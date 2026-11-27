<script setup lang="ts">
import { computed, nextTick, ref } from 'vue';
import {
  ArrowUp,
  AudioLines,
  Eye,
  Film,
  Image as ImageIcon,
  Palette,
  Play,
  Plus,
  Sparkles,
  X,
} from 'lucide-vue-next';
import { apiClient } from '@/api/client';
import { NODE_DEFS } from '@/data/nodeDefs';
import { useCanvasStore } from '@/stores/canvas';
import type { CanvasCard, LocalMediaRef, VideoGenMode } from '@/types/canvas';

interface MediaSlot {
  key: string;
  tag: string; // e.g. "@图片1", "@视频1", "@音频1"
  shortLabel: string; // e.g. "图片1", "视频1", "音频1"
  kind: 'image' | 'video' | 'audio';
  index: number;
  source: 'upstream' | 'local';
  fromCardId?: string;
  localId?: string;
  name: string;
  url?: string;
  ready: boolean;
}

const props = defineProps<{
  card: CanvasCard;
}>();

const canvas = useCanvasStore();
const fileInput = ref<HTMLInputElement | null>(null);
const textareaRef = ref<HTMLTextAreaElement | null>(null);
const backdropRef = ref<HTMLDivElement | null>(null);
const uploading = ref(false);
const previewSlot = ref<MediaSlot | null>(null);
const mentionOpen = ref(false);
const mentionQuery = ref('');
const mentionStart = ref(-1);
const mentionActiveIdx = ref(0);

const def = computed(() => NODE_DEFS.video);

const modeTabs: Array<{ id: VideoGenMode; label: string }> = [
  { id: 'omni', label: '全能参考' },
  { id: 't2v', label: '文生视频' },
  { id: 'firstFrame', label: '首帧' },
  { id: 'firstLastFrame', label: '首尾帧' },
];

const currentMode = computed<VideoGenMode>({
  get: () => props.card.videoMode || 'omni',
  set: (val) => {
    props.card.videoMode = val;
    notifyChange();
  },
});

const currentModel = computed({
  get: () => props.card.model || def.value.defaultModel || 'seedance-2.0-mini',
  set: (val: string) => {
    props.card.model = val;
    notifyChange();
  },
});

const currentResolution = computed({
  get: () => props.card.resolution || '720p',
  set: (val: string) => {
    props.card.resolution = val;
    notifyChange();
  },
});

const currentDuration = computed({
  get: () => props.card.duration || 15,
  set: (val: number) => {
    props.card.duration = val;
    notifyChange();
  },
});

const currentAspect = computed({
  get: () => props.card.aspect || '16:9',
  set: (val: string) => {
    props.card.aspect = val;
    notifyChange();
  },
});

const currentStyle = computed({
  get: () => props.card.stylePreset || '',
  set: (val: string) => {
    props.card.stylePreset = val;
    notifyChange();
  },
});

const styleOptions = ['', '古风奇幻', '电影写实', '3D 动画', '赛博霓虹', '水墨国风', '二次元'];

// Aggregate upstream connected media + direct local uploads into sequential slots
const mediaSlots = computed<MediaSlot[]>(() => {
  const images: MediaSlot[] = [];
  const videos: MediaSlot[] = [];
  const audios: MediaSlot[] = [];

  const pushSlot = (
    kind: 'image' | 'video' | 'audio',
    payload: Omit<MediaSlot, 'tag' | 'shortLabel' | 'index' | 'kind'>,
  ) => {
    const bucket = kind === 'image' ? images : kind === 'video' ? videos : audios;
    const prefix = kind === 'image' ? '图片' : kind === 'video' ? '视频' : '音频';
    const index = bucket.length + 1;
    bucket.push({
      ...payload,
      kind,
      index,
      shortLabel: `${prefix}${index}`,
      tag: `@${prefix}${index}`,
    });
  };

  for (const refItem of props.card.refs || []) {
    if (refItem.kind !== 'image' && refItem.kind !== 'video' && refItem.kind !== 'audio') continue;
    const up = canvas.cards.find((c) => c.id === refItem.from);
    if (!up) continue;
    const results = up.results || [];
    const activeIdx =
      typeof up.activeResultIndex === 'number' && up.activeResultIndex >= 0 && up.activeResultIndex < results.length
        ? up.activeResultIndex
        : results.length - 1;
    const active = activeIdx >= 0 ? results[activeIdx] : null;
    const url = active?.url || active?.dataUrl || '';
    pushSlot(refItem.kind, {
      key: `up-${up.id}-${refItem.kind}`,
      source: 'upstream',
      fromCardId: up.id,
      name: up.name || refItem.label || '上游素材',
      url: url || undefined,
      ready: Boolean(url),
    });
  }

  for (const local of props.card.localMedia || []) {
    pushSlot(local.kind, {
      key: `loc-${local.id}`,
      source: 'local',
      localId: local.id,
      name: local.name,
      url: local.url,
      ready: Boolean(local.url),
    });
  }

  return [...images, ...videos, ...audios];
});

const imageSlots = computed(() => mediaSlots.value.filter((s) => s.kind === 'image'));
const videoSlots = computed(() => mediaSlots.value.filter((s) => s.kind === 'video'));
const audioSlots = computed(() => mediaSlots.value.filter((s) => s.kind === 'audio'));

const visibleSlots = computed(() => {
  if (currentMode.value === 't2v') return [];
  if (currentMode.value === 'firstFrame') return imageSlots.value.slice(0, 1);
  if (currentMode.value === 'firstLastFrame') return imageSlots.value.slice(0, 2);
  return mediaSlots.value;
});

const usedTags = computed(() => {
  const text = props.card.prompt || '';
  const set = new Set<string>();
  const re = /@(图片|视频|音频)(\d+)/g;
  let m: RegExpExecArray | null;
  while ((m = re.exec(text)) !== null) {
    set.add(m[0]);
  }
  return set;
});

const filteredMentionSlots = computed(() => {
  const q = mentionQuery.value.trim().toLowerCase();
  if (!q) return mediaSlots.value;
  return mediaSlots.value.filter(
    (s) =>
      s.shortLabel.toLowerCase().includes(q) ||
      s.tag.toLowerCase().includes(q) ||
      s.name.toLowerCase().includes(q),
  );
});

const highlightedPromptHtml = computed(() => {
  const raw = props.card.prompt || '';
  if (!raw) return '';
  const escaped = raw
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;');
  const validTags = new Set(mediaSlots.value.map((s) => s.tag));
  const highlighted = escaped.replace(/@(图片|视频|音频)(\d+)/g, (match, typeName) => {
    const cls =
      typeName === '音频'
        ? 'mention-audio'
        : typeName === '视频'
          ? 'mention-video'
          : 'mention-image';
    const missing = validTags.size > 0 && !validTags.has(match) ? ' mention-missing' : '';
    return `<mark class="mention-mark ${cls}${missing}">${match}</mark>`;
  });
  return `${highlighted}\n`;
});

function notifyChange() {
  canvas.markDownstreamStale(props.card.id, `上游「${props.card.name}」参数已修改`);
  canvas.scheduleSave();
}

function syncScroll() {
  if (!textareaRef.value || !backdropRef.value) return;
  backdropRef.value.scrollTop = textareaRef.value.scrollTop;
  backdropRef.value.scrollLeft = textareaRef.value.scrollLeft;
}

function handlePromptInput() {
  notifyChange();
  syncScroll();
  const el = textareaRef.value;
  if (!el) return;
  const pos = el.selectionStart ?? 0;
  const before = (props.card.prompt || '').slice(0, pos);
  const match = before.match(/@([^\s@]{0,12})$/);
  if (match && mediaSlots.value.length > 0) {
    mentionOpen.value = true;
    mentionQuery.value = match[1];
    mentionStart.value = pos - match[0].length;
    mentionActiveIdx.value = 0;
  } else {
    mentionOpen.value = false;
    mentionStart.value = -1;
  }
}

function handlePromptKeydown(e: KeyboardEvent) {
  if (!mentionOpen.value || !filteredMentionSlots.value.length) return;
  if (e.key === 'ArrowDown') {
    e.preventDefault();
    mentionActiveIdx.value = (mentionActiveIdx.value + 1) % filteredMentionSlots.value.length;
  } else if (e.key === 'ArrowUp') {
    e.preventDefault();
    mentionActiveIdx.value =
      (mentionActiveIdx.value - 1 + filteredMentionSlots.value.length) % filteredMentionSlots.value.length;
  } else if (e.key === 'Enter' || e.key === 'Tab') {
    e.preventDefault();
    applyMention(filteredMentionSlots.value[mentionActiveIdx.value]);
  } else if (e.key === 'Escape') {
    e.preventDefault();
    mentionOpen.value = false;
  }
}

function applyMention(slot: MediaSlot) {
  const el = textareaRef.value;
  const text = props.card.prompt || '';
  const pos = el?.selectionStart ?? text.length;
  const start = mentionStart.value >= 0 ? mentionStart.value : pos;
  const insertText = `${slot.tag} `;
  props.card.prompt = text.slice(0, start) + insertText + text.slice(pos);
  mentionOpen.value = false;
  mentionStart.value = -1;
  notifyChange();
  void nextTick(() => {
    if (!el) return;
    const nextPos = start + insertText.length;
    el.focus();
    el.setSelectionRange(nextPos, nextPos);
    syncScroll();
  });
}

function insertTagAtCursor(slot: MediaSlot) {
  const el = textareaRef.value;
  const text = props.card.prompt || '';
  const start = el?.selectionStart ?? text.length;
  const end = el?.selectionEnd ?? text.length;
  const insertText = `${slot.tag}`;
  props.card.prompt = text.slice(0, start) + insertText + text.slice(end);
  notifyChange();
  void nextTick(() => {
    if (!el) return;
    const nextPos = start + insertText.length;
    el.focus();
    el.setSelectionRange(nextPos, nextPos);
    syncScroll();
  });
}

function applySmartReference() {
  const slots = mediaSlots.value;
  if (!slots.length) {
    canvas.showToast('请先连接上游素材节点或点击「+」上传参考素材');
    return;
  }
  const parts: string[] = [];
  if (audioSlots.value.length) {
    parts.push(`${audioSlots.value[0].tag} 作为背景配乐与节奏基准，画面切换紧贴音频节拍。`);
  }
  if (videoSlots.value.length) {
    parts.push(
      `${videoSlots.value[0].tag} 用于整体动作轨迹、运镜速度与镜头切换节奏，不直接照搬原视频人物外观。`,
    );
  }
  if (imageSlots.value.length) {
    const imgDescs = imageSlots.value.map((s, idx) => {
      if (idx === 0) return `${s.tag}（${s.name}）用于锁定核心主体外观与服装细节`;
      return `${s.tag}（${s.name}）用于第 ${idx + 1} 段场景构图与氛围参考`;
    });
    parts.push(imgDescs.join('；') + '。');
  }
  const existing = (props.card.prompt || '').trim();
  const generated = parts.join('\n\n');
  props.card.prompt = existing ? `${existing}\n\n${generated}` : generated;
  notifyChange();
  canvas.showToast(`已智能引用 ${slots.length} 项参考素材`);
}

function removeSlot(slot: MediaSlot) {
  if (slot.source === 'local' && slot.localId) {
    props.card.localMedia = (props.card.localMedia || []).filter((m) => m.id !== slot.localId);
    notifyChange();
  } else if (slot.source === 'upstream' && slot.fromCardId) {
    canvas.disconnectEdge(slot.fromCardId, props.card.id, slot.kind);
  }
}

async function handleDirectUpload(event: Event) {
  const input = event.target as HTMLInputElement;
  const files = Array.from(input.files || []);
  input.value = '';
  if (!files.length) return;
  uploading.value = true;
  try {
    if (!props.card.localMedia) props.card.localMedia = [];
    for (const file of files) {
      const res = await apiClient.uploadAsset(file);
      const item: LocalMediaRef = {
        id: `lm-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 6)}`,
        kind: res.asset.kind,
        name: res.asset.name,
        url: res.asset.url,
        path: res.asset.path,
      };
      props.card.localMedia.push(item);
    }
    notifyChange();
    canvas.showToast(`已添加 ${files.length} 个参考素材`);
  } catch (err: any) {
    canvas.showToast(`上传失败：${err?.message || '未知错误'}`);
  } finally {
    uploading.value = false;
  }
}

function cycleBatch() {
  const cur = props.card.batchCount || 1;
  props.card.batchCount = cur === 1 ? 2 : cur === 2 ? 4 : 1;
  notifyChange();
}

function triggerUpload() {
  fileInput.value?.click();
}

function closeMentionSoon() {
  window.setTimeout(() => {
    mentionOpen.value = false;
  }, 140);
}
</script>

<template>
  <div class="omni-panel" @pointerdown.stop>
    <!-- Top Mode Tabs + Smart Reference -->
    <div class="omni-top">
      <div class="mode-tabs">
        <button
          v-for="tab in modeTabs"
          :key="tab.id"
          type="button"
          class="mode-tab"
          :class="{ active: currentMode === tab.id }"
          @click="currentMode = tab.id"
        >
          {{ tab.label }}
        </button>
      </div>
      <button
        type="button"
        class="smart-ref-btn"
        title="根据当前挂载的图片/视频/音频自动生成 @引用 描述"
        @click="applySmartReference"
      >
        <Sparkles :size="12" />
        <span>智能引用</span>
      </button>
    </div>

    <!-- Media Reference Slot Tray -->
    <div v-if="currentMode !== 't2v'" class="media-tray">
      <div
        v-for="slot in visibleSlots"
        :key="slot.key"
        class="media-tile"
        :class="[
          `kind-${slot.kind}`,
          { used: usedTags.has(slot.tag), placeholder: !slot.ready },
        ]"
        :title="`点击插入 ${slot.tag}（${slot.name}）`"
        @click="insertTagAtCursor(slot)"
      >
        <img v-if="slot.kind === 'image' && slot.url" :src="slot.url" :alt="slot.name" class="tile-img" />
        <div v-else-if="slot.kind === 'video'" class="tile-video-bg">
          <video v-if="slot.url" :src="slot.url" muted class="tile-img" />
          <div class="play-badge"><Play :size="14" /></div>
        </div>
        <div v-else-if="slot.kind === 'audio'" class="tile-audio-bg">
          <AudioLines :size="20" />
        </div>
        <div v-else class="tile-empty">
          <ImageIcon v-if="slot.kind === 'image'" :size="16" />
          <Film v-else :size="16" />
          <span class="tile-empty-name">{{ slot.name.slice(0, 4) }}</span>
        </div>

        <span class="tile-tag">
          {{
            currentMode === 'firstFrame'
              ? '首帧'
              : currentMode === 'firstLastFrame'
                ? slot.index === 1
                  ? '首帧'
                  : '尾帧'
                : slot.shortLabel
          }}
        </span>

        <button
          v-if="slot.kind === 'image' && slot.url"
          type="button"
          class="tile-action preview"
          title="查看原图"
          @click.stop="previewSlot = slot"
        >
          <Eye :size="10" />
        </button>
        <button
          type="button"
          class="tile-action remove"
          :title="slot.source === 'upstream' ? '断开上游连线' : '移除素材'"
          @click.stop="removeSlot(slot)"
        >
          <X :size="10" />
        </button>
      </div>

      <!-- Add / Upload Slot -->
      <button
        type="button"
        class="media-tile add-tile"
        :disabled="uploading"
        title="上传参考图片 / 视频 / 音频（也可直接从左侧连线接入）"
        @click="triggerUpload"
      >
        <Plus :size="18" />
        <span>{{ uploading ? '…' : '添加' }}</span>
      </button>
      <input
        ref="fileInput"
        type="file"
        multiple
        accept="image/*,video/*,audio/*,.mp4,.mov,.webm,.mkv,.m4v,.avi,.mp3,.wav,.m4a,.aac,.ogg,.flac,.opus"
        hidden
        @change="handleDirectUpload"
      />
    </div>

    <!-- Rich @-Mention Prompt Editor -->
    <div class="prompt-wrap">
      <div ref="backdropRef" class="prompt-backdrop" aria-hidden="true" v-html="highlightedPromptHtml" />
      <textarea
        ref="textareaRef"
        v-model="card.prompt"
        class="prompt-input"
        rows="5"
        placeholder="输入视频描述，键入 @ 可快速引用 @图片1、@视频1、@音频1，或点击上方素材卡片插入…"
        @input="handlePromptInput"
        @scroll="syncScroll"
        @keydown="handlePromptKeydown"
        @blur="closeMentionSoon"
      />

      <!-- @ Autocomplete Popover -->
      <div v-if="mentionOpen && filteredMentionSlots.length" class="mention-menu">
        <button
          v-for="(slot, idx) in filteredMentionSlots"
          :key="slot.key"
          type="button"
          class="mention-item"
          :class="{ active: idx === mentionActiveIdx }"
          @mousedown.prevent="applyMention(slot)"
        >
          <span class="m-dot" :class="`kind-${slot.kind}`" />
          <b class="m-tag">{{ slot.tag }}</b>
          <span class="m-name">{{ slot.name }}</span>
        </button>
      </div>
    </div>

    <!-- Quick Insert Tag Strip -->
    <div v-if="mediaSlots.length && currentMode !== 't2v'" class="tag-strip">
      <span class="tag-strip-label">点击插入：</span>
      <button
        v-for="slot in mediaSlots"
        :key="`pill-${slot.key}`"
        type="button"
        class="ref-pill"
        :class="[`kind-${slot.kind}`, { used: usedTags.has(slot.tag) }]"
        @click="insertTagAtCursor(slot)"
      >
        {{ slot.tag }}
      </button>
    </div>

    <!-- Bottom Parameter & Run Bar -->
    <div class="omni-bottom">
      <div class="bottom-left">
        <select
          v-model="currentModel"
          class="bar-select model-select"
          title="选择视频生成模型"
        >
          <option v-for="m in def.models" :key="m.id" :value="m.id">{{ m.label }}</option>
        </select>

        <div class="param-pill">
          <select v-model="currentResolution" class="inline-select" title="分辨率">
            <option value="720p">720p</option>
            <option value="1080p">1080p</option>
          </select>
          <span class="sep">/</span>
          <select v-model.number="currentDuration" class="inline-select" title="时长">
            <option :value="4">4秒</option>
            <option :value="5">5秒</option>
            <option :value="10">10秒</option>
            <option :value="15">15秒</option>
            <option :value="20">20秒</option>
            <option :value="30">30秒</option>
          </select>
          <span class="sep">/</span>
          <select v-model="currentAspect" class="inline-select" title="画面比例">
            <option value="16:9">16:9</option>
            <option value="9:16">9:16</option>
            <option value="1:1">1:1</option>
          </select>
        </div>

        <label class="style-pill" title="画面风格">
          <Palette :size="12" />
          <select v-model="currentStyle" class="inline-select">
            <option value="">风格</option>
            <option v-for="st in styleOptions.slice(1)" :key="st" :value="st">{{ st }}</option>
          </select>
        </label>
      </div>

      <div class="bottom-right">
        <button
          type="button"
          class="batch-btn"
          title="切换生成数量"
          @click="cycleBatch"
        >
          {{ card.batchCount || 1 }}x
        </button>

        <button
          type="button"
          class="run-pill-btn"
          :disabled="card.status === 'running'"
          title="运行全能参考视频生成"
          @click="canvas.runSingleNode(card.id)"
        >
          <span>{{ card.status === 'running' ? '生成中' : '生成' }}</span>
          <span class="run-arrow"><ArrowUp :size="13" /></span>
        </button>
      </div>
    </div>

    <!-- Image Lightbox -->
    <Teleport to="body">
      <div v-if="previewSlot" class="lightbox-mask" @click="previewSlot = null">
        <div class="lightbox-card" @click.stop>
          <header>
            <span>{{ previewSlot.tag }} · {{ previewSlot.name }}</span>
            <button type="button" class="icon-close" @click="previewSlot = null"><X :size="16" /></button>
          </header>
          <img :src="previewSlot.url" :alt="previewSlot.name" />
        </div>
      </div>
    </Teleport>
  </div>
</template>

<style scoped>
.omni-panel {
  display: grid;
  gap: 10px;
}

.omni-top {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}

.mode-tabs {
  display: inline-flex;
  align-items: center;
  gap: 2px;
  padding: 2px;
  border-radius: 9px;
  background: rgba(255, 255, 255, 0.04);
}

.mode-tab {
  border: 0;
  background: transparent;
  color: var(--muted);
  font-size: 12px;
  padding: 4px 10px;
  border-radius: 7px;
  transition: background 0.14s ease, color 0.14s ease;
}

.mode-tab:hover {
  color: var(--text);
}

.mode-tab.active {
  background: rgba(255, 255, 255, 0.12);
  color: #fff;
  font-weight: 600;
}

.smart-ref-btn {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 4px 10px;
  border-radius: 8px;
  border: 1px solid var(--line-strong);
  background: rgba(255, 255, 255, 0.04);
  color: var(--text);
  font-size: 11.5px;
}

.smart-ref-btn:hover {
  border-color: rgba(244, 224, 77, 0.55);
  color: #f4e04d;
  background: rgba(244, 224, 77, 0.08);
}

.media-tray {
  display: flex;
  align-items: center;
  gap: 7px;
  overflow-x: auto;
  padding: 2px 1px 4px;
}

.media-tile {
  position: relative;
  width: 54px;
  height: 54px;
  border-radius: 10px;
  flex-shrink: 0;
  overflow: hidden;
  border: 1px solid var(--line-strong);
  background: #14171c;
  cursor: pointer;
  transition: transform 0.12s ease, border-color 0.12s ease;
}

.media-tile:hover {
  transform: translateY(-1px);
  border-color: rgba(244, 224, 77, 0.7);
}

.media-tile.used {
  border-color: rgba(244, 224, 77, 0.75);
  box-shadow: 0 0 0 1px rgba(244, 224, 77, 0.25);
}

.media-tile.placeholder {
  border-style: dashed;
}

.tile-img {
  width: 100%;
  height: 100%;
  object-fit: cover;
  display: block;
}

.tile-video-bg {
  position: relative;
  width: 100%;
  height: 100%;
  background: radial-gradient(circle at center, #333a45, #12151a);
  display: grid;
  place-items: center;
}

.play-badge {
  position: absolute;
  width: 24px;
  height: 24px;
  border-radius: 50%;
  background: rgba(255, 255, 255, 0.88);
  color: #111317;
  display: grid;
  place-items: center;
}

.tile-audio-bg {
  width: 100%;
  height: 100%;
  background: linear-gradient(135deg, #6d28d9, #9333ea 55%, #a855f7);
  color: #fff;
  display: grid;
  place-items: center;
  padding-bottom: 10px;
}

.tile-empty {
  width: 100%;
  height: 100%;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 2px;
  padding-bottom: 12px;
  color: var(--muted);
}

.tile-empty-name {
  font-size: 9px;
  max-width: 46px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.tile-tag {
  position: absolute;
  left: 0;
  right: 0;
  bottom: 0;
  padding: 1px 4px;
  background: rgba(12, 14, 18, 0.78);
  color: #f3f5f7;
  font-size: 10px;
  text-align: center;
  line-height: 1.35;
}

.tile-action {
  position: absolute;
  top: 2px;
  width: 16px;
  height: 16px;
  border-radius: 50%;
  border: 0;
  background: rgba(12, 14, 18, 0.82);
  color: #fff;
  display: grid;
  place-items: center;
  opacity: 0;
  transition: opacity 0.12s ease;
}

.media-tile:hover .tile-action {
  opacity: 1;
}

.tile-action.remove {
  right: 2px;
}

.tile-action.remove:hover {
  background: var(--danger);
}

.tile-action.preview {
  left: 2px;
}

.add-tile {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 2px;
  border: 1px dashed var(--line-strong);
  background: rgba(255, 255, 255, 0.03);
  color: var(--muted);
  font-size: 10px;
}

.add-tile:hover {
  color: var(--text);
  border-color: var(--accent-border);
  background: rgba(116, 213, 139, 0.07);
}

/* Rich highlight prompt editor */
.prompt-wrap {
  position: relative;
  border-radius: 10px;
  border: 1px solid var(--line);
  background: #14171c;
}

.prompt-wrap:focus-within {
  border-color: rgba(244, 224, 77, 0.45);
}

.prompt-backdrop,
.prompt-input {
  width: 100%;
  min-height: 112px;
  max-height: 220px;
  margin: 0;
  padding: 10px 11px;
  font-family: inherit;
  font-size: 12.5px;
  line-height: 1.62;
  letter-spacing: normal;
  white-space: pre-wrap;
  word-wrap: break-word;
  overflow-y: auto;
  border-radius: 10px;
}

.prompt-backdrop {
  position: absolute;
  inset: 0;
  color: #e5e9f0;
  pointer-events: none;
  overflow: hidden;
}

.prompt-input {
  position: relative;
  display: block;
  border: 0;
  outline: none;
  resize: vertical;
  background: transparent;
  color: transparent;
  caret-color: #f4e04d;
  user-select: text;
}

.prompt-input::placeholder {
  color: #6e7a8a;
}

.prompt-backdrop :deep(.mention-mark) {
  background: rgba(244, 224, 77, 0.16);
  color: #f4e04d;
  border-radius: 4px;
  font-weight: 600;
}

.prompt-backdrop :deep(.mention-audio) {
  background: rgba(168, 85, 247, 0.24);
  color: #f4e04d;
}

.prompt-backdrop :deep(.mention-video) {
  background: rgba(189, 140, 255, 0.2);
  color: #f4e04d;
}

.prompt-backdrop :deep(.mention-missing) {
  background: rgba(255, 123, 114, 0.22);
  color: #ff9c95;
  text-decoration: underline wavy rgba(255, 123, 114, 0.7);
}

.mention-menu {
  position: absolute;
  left: 10px;
  top: calc(100% + 4px);
  z-index: 40;
  min-width: 210px;
  max-height: 180px;
  overflow-y: auto;
  padding: 4px;
  border-radius: 10px;
  background: #1b1f26;
  border: 1px solid var(--line-strong);
  box-shadow: var(--shadow);
  display: grid;
  gap: 2px;
}

.mention-item {
  display: flex;
  align-items: center;
  gap: 7px;
  padding: 6px 8px;
  border: 0;
  border-radius: 7px;
  background: transparent;
  text-align: left;
  font-size: 12px;
}

.mention-item:hover,
.mention-item.active {
  background: rgba(244, 224, 77, 0.14);
}

.m-dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  flex-shrink: 0;
}

.m-dot.kind-image {
  background: #62b5ff;
}

.m-dot.kind-video {
  background: #bd8cff;
}

.m-dot.kind-audio {
  background: #f59a56;
}

.m-tag {
  color: #f4e04d;
  font-weight: 600;
}

.m-name {
  color: var(--muted);
  font-size: 11px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.tag-strip {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 5px;
}

.tag-strip-label {
  font-size: 10.5px;
  color: var(--muted);
}

.ref-pill {
  padding: 2px 7px;
  border-radius: 999px;
  border: 1px solid var(--line);
  background: rgba(255, 255, 255, 0.04);
  color: var(--muted);
  font-size: 11px;
}

.ref-pill:hover {
  border-color: rgba(244, 224, 77, 0.55);
  color: #f4e04d;
}

.ref-pill.used {
  border-color: rgba(244, 224, 77, 0.45);
  background: rgba(244, 224, 77, 0.12);
  color: #f4e04d;
}

.omni-bottom {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  padding-top: 6px;
  border-top: 1px solid var(--line);
  flex-wrap: wrap;
}

.bottom-left,
.bottom-right {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
}

.bar-select,
.param-pill,
.style-pill,
.batch-btn {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  height: 28px;
  padding: 0 8px;
  border-radius: 8px;
  border: 1px solid var(--line);
  background: rgba(255, 255, 255, 0.04);
  color: var(--text);
  font-size: 11.5px;
}

.model-select {
  max-width: 150px;
}

.inline-select {
  border: 0;
  background: transparent;
  color: inherit;
  outline: none;
  font-size: 11.5px;
  cursor: pointer;
}

.inline-select option,
.bar-select option {
  background: #181b20;
  color: #f3f5f7;
}

.sep {
  color: var(--muted);
  font-size: 11px;
}

.batch-btn {
  color: var(--muted);
}

.batch-btn:hover {
  color: var(--text);
  border-color: var(--line-strong);
}

.run-pill-btn {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  height: 30px;
  padding: 0 5px 0 11px;
  border-radius: 999px;
  border: 1px solid var(--line-strong);
  background: #232730;
  color: #fff;
  font-size: 12px;
  font-weight: 600;
}

.run-pill-btn:hover:not(:disabled) {
  border-color: var(--accent-border);
  background: #2b313d;
}

.run-pill-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.run-arrow {
  width: 22px;
  height: 22px;
  border-radius: 50%;
  background: #f3f5f7;
  color: #111317;
  display: grid;
  place-items: center;
}

.lightbox-mask {
  position: fixed;
  inset: 0;
  z-index: 300;
  background: rgba(0, 0, 0, 0.72);
  display: grid;
  place-items: center;
  padding: 24px;
}

.lightbox-card {
  max-width: min(86vw, 900px);
  max-height: 86vh;
  background: #181b20;
  border: 1px solid var(--line-strong);
  border-radius: 14px;
  overflow: hidden;
  display: flex;
  flex-direction: column;
}

.lightbox-card header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 10px 14px;
  border-bottom: 1px solid var(--line);
  font-size: 13px;
}

.lightbox-card img {
  display: block;
  max-width: 100%;
  max-height: 76vh;
  object-fit: contain;
  background: #0d1014;
}

.icon-close {
  border: 0;
  background: transparent;
  color: var(--muted);
  display: grid;
  place-items: center;
}
</style>
