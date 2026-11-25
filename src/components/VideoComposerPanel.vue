<script setup lang="ts">
import { computed, nextTick, onMounted, ref, watch } from 'vue';
import {
  Plus,
  X,
  Play,
  Maximize2,
  Minimize2,
  ChevronDown,
  Palette,
  Sparkles,
  ArrowUp,
  Upload,
  Search,
  Check,
  Loader2,
  Wand2,
  Music,
  Film,
  Image as ImageIcon,
} from 'lucide-vue-next';
import { apiClient } from '@/api/client';
import { NODE_DEFS } from '@/data/nodeDefs';
import { useCanvasStore } from '@/stores/canvas';
import type { CanvasCard, VideoGenMode } from '@/types/canvas';

interface ResolvedRefItem {
  key: string;
  tag: string; // e.g. "@图片1"
  label: string; // e.g. "图片1"
  nodeName: string;
  kind: 'image' | 'video' | 'audio';
  thumbUrl?: string;
  sourceType: 'edge' | 'local';
  fromCardId?: string;
  localId?: string;
}

const props = defineProps<{
  card: CanvasCard;
}>();

const canvas = useCanvasStore();

const editorRef = ref<HTMLElement | null>(null);
const uploadInput = ref<HTMLInputElement | null>(null);

const expanded = ref(false);
const activePopover = ref<'model' | 'params' | 'style' | 'batch' | null>(null);
const pickerOpen = ref(false);
const pickerFilter = ref<'all' | 'image' | 'video' | 'audio'>('all');
const pickerSearch = ref('');
const uploading = ref(false);
const activeProvider = ref<'runninghub' | 'ark' | 'tensor'>('tensor');
const elapsedSec = ref(0);
let elapsedTimer: ReturnType<typeof setInterval> | null = null;

watch(
  () => props.card.status,
  (st) => {
    if (st === 'running') {
      elapsedSec.value = 0;
      if (elapsedTimer) clearInterval(elapsedTimer);
      elapsedTimer = setInterval(() => {
        elapsedSec.value += 1;
      }, 1000);
    } else {
      if (elapsedTimer) {
        clearInterval(elapsedTimer);
        elapsedTimer = null;
      }
    }
  },
  { immediate: true },
);

// @ mention autocomplete state
const mentionOpen = ref(false);
const mentionQuery = ref('');
const mentionIndex = ref(0);

const modes: Array<{ id: VideoGenMode; label: string }> = [
  { id: 'omni', label: '全能参考' },
  { id: 't2v', label: '文生视频' },
  { id: 'firstFrame', label: '首帧' },
  { id: 'firstLastFrame', label: '首尾帧' },
];

const providerTabs: Array<{ id: 'tensor' | 'runninghub' | 'ark'; label: string; short: string }> = [
  { id: 'tensor', label: 'Tensor', short: 'Tensor' },
  { id: 'runninghub', label: 'RunningHub', short: 'RH' },
  { id: 'ark', label: '火山方舟', short: '火山' },
];

const models = computed(() => NODE_DEFS.video.models || []);
const filteredModels = computed(() =>
  models.value.filter((item) => (item.provider || 'ark') === activeProvider.value),
);
const providerShortLabel = computed(
  () => providerTabs.find((p) => p.id === activeProvider.value)?.short || 'Tensor',
);
const currentModelLabel = computed(() => {
  const inChannel = filteredModels.value.find((item) => item.id === props.card.model);
  if (inChannel) return inChannel.label;
  const anyMatch = models.value.find((item) => item.id === props.card.model);
  return anyMatch?.label || filteredModels.value[0]?.label || 'Seedance 2.5 Flash';
});

async function switchChannel(ch: 'tensor' | 'runninghub' | 'ark') {
  activeProvider.value = ch;
  const list = models.value.filter((item) => (item.provider || 'ark') === ch);
  if (list.length && !list.some((m) => m.id === props.card.model)) {
    props.card.model = list[0].id;
    canvas.scheduleSave();
  }
  try {
    await apiClient.saveConfig({ video_provider: ch });
  } catch {
    // ignore transient save error
  }
}

const styleOptions = ['无', '古风奇幻', '电影写实', '3D 动画', '国风仙侠', '赛博霓虹', '胶片质感'];
const aspectOptions = ['自适应', '16:9', '4:3', '1:1', '3:4', '9:16', '21:9'];
const resolutionOptions = ['480p', '720p', '1080p'];

const currentMode = computed<VideoGenMode>({
  get: () => props.card.videoMode || 'omni',
  set: (v) => {
    props.card.videoMode = v;
    canvas.scheduleSave();
  },
});

function pickMediaResult(card: CanvasCard) {
  const results = card.results || [];
  if (!results.length) return undefined;
  const active = results[card.activeResultIndex ?? results.length - 1];
  if (active && active.kind !== 'text') return active;
  for (let i = results.length - 1; i >= 0; i -= 1) {
    if (results[i] && results[i].kind !== 'text') return results[i];
  }
  return active;
}

// Build unified numbered reference items: 图片1..N, 视频1..N, 音频1..N
const refItems = computed<ResolvedRefItem[]>(() => {
  const images: ResolvedRefItem[] = [];
  const videos: ResolvedRefItem[] = [];
  const audios: ResolvedRefItem[] = [];

  for (const r of props.card.refs || []) {
    const up = canvas.cards.find((c) => c.id === r.from);
    if (!up) continue;
    const latest = pickMediaResult(up);
    const kind: 'image' | 'video' | 'audio' | null =
      r.kind === 'image' || r.kind === 'video' || r.kind === 'audio'
        ? r.kind
        : latest?.kind && latest.kind !== 'text'
          ? latest.kind
          : up.type === 'video'
            ? 'video'
            : up.type === 'audio'
              ? 'audio'
              : up.mediaKind || null;
    if (!kind) continue;
    const thumbUrl = latest?.url || latest?.dataUrl || undefined;
    const base = {
      key: `edge-${up.id}-${kind}`,
      nodeName: up.name || r.label || '素材',
      kind,
      thumbUrl,
      sourceType: 'edge' as const,
      fromCardId: up.id,
    };
    if (kind === 'image') {
      const idx = images.length + 1;
      images.push({ ...base, label: `图片${idx}`, tag: `@图片${idx}` });
    } else if (kind === 'video') {
      const idx = videos.length + 1;
      videos.push({ ...base, label: `视频${idx}`, tag: `@视频${idx}` });
    } else {
      const idx = audios.length + 1;
      audios.push({ ...base, label: `音频${idx}`, tag: `@音频${idx}` });
    }
  }

  for (const lm of props.card.localMedia || []) {
    const base = {
      key: `local-${lm.id}`,
      nodeName: lm.name,
      kind: lm.kind,
      thumbUrl: lm.url,
      sourceType: 'local' as const,
      localId: lm.id,
    };
    if (lm.kind === 'image') {
      const idx = images.length + 1;
      images.push({ ...base, label: `图片${idx}`, tag: `@图片${idx}` });
    } else if (lm.kind === 'video') {
      const idx = videos.length + 1;
      videos.push({ ...base, label: `视频${idx}`, tag: `@视频${idx}` });
    } else {
      const idx = audios.length + 1;
      audios.push({ ...base, label: `音频${idx}`, tag: `@音频${idx}` });
    }
  }

  return [...images, ...videos, ...audios];
});

const refLookup = computed(() => {
  const map = new Map<string, ResolvedRefItem>();
  for (const item of refItems.value) {
    map.set(item.tag, item);
  }
  return map;
});

const filteredMentions = computed(() => {
  const q = mentionQuery.value.trim().toLowerCase();
  if (!q) return refItems.value;
  return refItems.value.filter(
    (item) =>
      item.label.toLowerCase().includes(q) ||
      item.nodeName.toLowerCase().includes(q) ||
      item.tag.toLowerCase().includes(q),
  );
});

// Canvas media candidates for the "+" Reference Picker Modal (canvas cards + local uploads)
const canvasMediaCandidates = computed(() => {
  const q = pickerSearch.value.trim().toLowerCase();
  const cardItems = canvas.cards
    .filter(
      (c) =>
        c.id !== props.card.id &&
        (c.type === 'source' || c.type === 'image' || c.type === 'video' || c.type === 'audio'),
    )
    .map((c) => {
      const latest = pickMediaResult(c);
      const kind: 'image' | 'video' | 'audio' =
        latest?.kind && latest.kind !== 'text'
          ? latest.kind
          : c.type === 'video'
            ? 'video'
            : c.type === 'audio'
              ? 'audio'
              : c.mediaKind || 'image';
      const attached = (props.card.refs || []).some((r) => r.from === c.id);
      return {
        key: `card-${c.id}`,
        sourceType: 'edge' as const,
        card: c,
        localId: undefined as string | undefined,
        name: c.name,
        kind,
        thumbUrl: latest?.url || latest?.dataUrl || '',
        attached,
      };
    });

  const localItems = (props.card.localMedia || []).map((lm) => ({
    key: `local-${lm.id}`,
    sourceType: 'local' as const,
    card: undefined as CanvasCard | undefined,
    localId: lm.id,
    name: lm.name,
    kind: lm.kind,
    thumbUrl: lm.url || '',
    attached: true,
  }));

  return [...localItems, ...cardItems].filter((item) => {
    if (pickerFilter.value !== 'all' && item.kind !== pickerFilter.value) return false;
    if (!q) return true;
    return item.name.toLowerCase().includes(q);
  });
});

function escapeHtml(str: string) {
  return str.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

function isVideoUrl(url?: string) {
  if (!url) return false;
  return /\.(mp4|mov|webm|mkv|m4v|avi|wmv|flv|3gp)(\?|$)/i.test(url);
}

function buildChipHtml(tag: string): string {
  const info = refLookup.value.get(tag);
  const kind = info?.kind || (tag.startsWith('@音频') ? 'audio' : tag.startsWith('@视频') ? 'video' : 'image');
  let iconHtml = '';
  if (kind === 'audio') {
    iconHtml = `<span class="m-thumb m-thumb-audio">♪</span>`;
  } else if (info?.thumbUrl && !isVideoUrl(info.thumbUrl)) {
    iconHtml = `<img class="m-thumb" src="${info.thumbUrl}" alt="" />`;
  } else {
    iconHtml = `<span class="m-thumb m-thumb-generic">${kind === 'video' ? '▶' : '▣'}</span>`;
  }
  return `<span class="mention-chip" contenteditable="false" data-mention="${escapeHtml(tag)}">${iconHtml}<span>${escapeHtml(tag)}</span></span>`;
}

function promptToHtml(text: string): string {
  if (!text) return '';
  const regex = /@(图片\d+|视频\d+|音频\d+)/g;
  let result = '';
  let lastIdx = 0;
  let match: RegExpExecArray | null;
  while ((match = regex.exec(text)) !== null) {
    const before = text.slice(lastIdx, match.index);
    result += escapeHtml(before).replace(/\n/g, '<br>');
    result += buildChipHtml(match[0]);
    lastIdx = match.index + match[0].length;
  }
  result += escapeHtml(text.slice(lastIdx)).replace(/\n/g, '<br>');
  return result;
}

function serializeEditor(root: HTMLElement): string {
  let out = '';
  const walk = (node: Node) => {
    if (node.nodeType === Node.TEXT_NODE) {
      out += node.textContent || '';
    } else if (node.nodeType === Node.ELEMENT_NODE) {
      const el = node as HTMLElement;
      if (el.dataset.mention) {
        out += el.dataset.mention;
        return;
      }
      if (el.tagName === 'BR') {
        out += '\n';
        return;
      }
      const isBlock = el.tagName === 'DIV' || el.tagName === 'P';
      if (isBlock && out.length > 0 && !out.endsWith('\n')) {
        out += '\n';
      }
      el.childNodes.forEach(walk);
    }
  };
  root.childNodes.forEach(walk);
  return out.replace(/\u200B/g, '');
}

let internalSync = false;

function renderEditorFromModel() {
  if (!editorRef.value) return;
  internalSync = true;
  editorRef.value.innerHTML = promptToHtml(props.card.prompt || '');
  internalSync = false;
}

watch(
  () => [props.card.id, refItems.value.map((r) => `${r.tag}:${r.thumbUrl || ''}`).join('|')],
  () => {
    void nextTick(() => renderEditorFromModel());
  },
);

onMounted(() => {
  renderEditorFromModel();
  void apiClient
    .getConfig()
    .then((res) => {
      activeProvider.value = (res.config.video_provider as 'runninghub' | 'ark' | 'tensor') || 'tensor';
    })
    .catch(() => {});
});

function detectMentionTrigger() {
  const sel = window.getSelection();
  if (!sel || !sel.rangeCount || !editorRef.value?.contains(sel.anchorNode)) {
    mentionOpen.value = false;
    return;
  }
  const node = sel.anchorNode;
  if (node && node.nodeType === Node.TEXT_NODE) {
    const before = (node.textContent || '').slice(0, sel.anchorOffset);
    const m = before.match(/@([^\s@]{0,12})$/);
    if (m) {
      mentionQuery.value = m[1];
      mentionIndex.value = 0;
      mentionOpen.value = true;
      return;
    }
  }
  mentionOpen.value = false;
}

function onEditorInput() {
  if (internalSync || !editorRef.value) return;
  props.card.prompt = serializeEditor(editorRef.value);
  canvas.scheduleSave();
  detectMentionTrigger();
}

function onEditorKeydown(event: KeyboardEvent) {
  if (mentionOpen.value && filteredMentions.value.length) {
    if (event.key === 'ArrowDown') {
      event.preventDefault();
      mentionIndex.value = (mentionIndex.value + 1) % filteredMentions.value.length;
      return;
    }
    if (event.key === 'ArrowUp') {
      event.preventDefault();
      mentionIndex.value =
        (mentionIndex.value - 1 + filteredMentions.value.length) % filteredMentions.value.length;
      return;
    }
    if (event.key === 'Enter' || event.key === 'Tab') {
      event.preventDefault();
      insertMentionTag(filteredMentions.value[mentionIndex.value].tag, true);
      return;
    }
    if (event.key === 'Escape') {
      event.preventDefault();
      mentionOpen.value = false;
      return;
    }
  }
}

function insertMentionTag(tag: string, replaceTrigger = false) {
  if (!editorRef.value) return;
  editorRef.value.focus();
  const sel = window.getSelection();
  if (replaceTrigger && sel && sel.rangeCount && sel.anchorNode?.nodeType === Node.TEXT_NODE) {
    const textNode = sel.anchorNode as Text;
    const offset = sel.anchorOffset;
    const before = textNode.textContent?.slice(0, offset) || '';
    const m = before.match(/@([^\s@]{0,12})$/);
    if (m) {
      const range = document.createRange();
      range.setStart(textNode, offset - m[0].length);
      range.setEnd(textNode, offset);
      sel.removeAllRanges();
      sel.addRange(range);
    }
  }

  const range = sel && sel.rangeCount && editorRef.value.contains(sel.anchorNode) ? sel.getRangeAt(0) : null;
  const temp = document.createElement('span');
  temp.innerHTML = buildChipHtml(tag);
  const chip = temp.firstElementChild as HTMLElement;
  const space = document.createTextNode(' ');

  if (range) {
    range.deleteContents();
    range.insertNode(space);
    range.insertNode(chip);
    range.setStartAfter(space);
    range.collapse(true);
    sel?.removeAllRanges();
    sel?.addRange(range);
  } else {
    editorRef.value.appendChild(chip);
    editorRef.value.appendChild(space);
  }

  mentionOpen.value = false;
  props.card.prompt = serializeEditor(editorRef.value);
  canvas.scheduleSave();
}

function handleSmartReference() {
  if (!refItems.value.length) {
    canvas.showToast('当前尚未连接参考素材，点击下方 + 号可添加图片/视频/音频');
    return;
  }
  const current = props.card.prompt || '';
  const missing = refItems.value.filter((item) => !current.includes(item.tag));
  if (!missing.length) {
    canvas.showToast('所有已连接素材均已在提示词中引用');
    return;
  }
  const additions = missing.map((item) => `${item.tag}（${item.nodeName}）`).join('、');
  props.card.prompt = current ? `${current}\n参考素材：${additions}` : `结合 ${additions} 生成高质感视频。`;
  renderEditorFromModel();
  canvas.scheduleSave();
  canvas.showToast(`已智能插入 ${missing.length} 个素材引用`);
}

function detachRef(item: ResolvedRefItem) {
  if (item.sourceType === 'edge' && item.fromCardId) {
    canvas.disconnectEdge(item.fromCardId, props.card.id, item.kind);
  } else if (item.sourceType === 'local' && item.localId) {
    props.card.localMedia = (props.card.localMedia || []).filter((m) => m.id !== item.localId);
    canvas.scheduleSave();
  }
}

function toggleCandidate(candidate: {
  sourceType: 'edge' | 'local';
  card?: CanvasCard;
  localId?: string;
  kind: 'image' | 'video' | 'audio';
  attached: boolean;
}) {
  if (candidate.sourceType === 'local' && candidate.localId) {
    props.card.localMedia = (props.card.localMedia || []).filter((m) => m.id !== candidate.localId);
    canvas.scheduleSave();
    return;
  }
  if (!candidate.card) return;
  if (candidate.attached) {
    canvas.disconnectEdge(candidate.card.id, props.card.id, candidate.kind);
  } else {
    canvas.connectNodes(candidate.card.id, props.card.id, candidate.kind, candidate.card.name);
  }
}

async function handleDirectUpload(event: Event) {
  const input = event.target as HTMLInputElement;
  const files = Array.from(input.files || []);
  input.value = '';
  if (!files.length) return;
  uploading.value = true;
  try {
    let added = 0;
    for (const file of files) {
      const res = await apiClient.uploadAsset(file);
      props.card.localMedia = [
        ...(props.card.localMedia || []),
        {
          id: `lm-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 6)}`,
          kind: res.asset.kind,
          name: res.asset.name,
          url: res.asset.url,
          path: res.asset.path,
        },
      ];
      added += 1;
    }
    canvas.scheduleSave();
    pickerOpen.value = false;
    canvas.showToast(`已添加 ${added} 个参考素材`);
  } catch (err: any) {
    canvas.showToast(`上传失败：${err?.message || '未知错误'}`);
  } finally {
    uploading.value = false;
  }
}

function togglePopover(name: 'model' | 'params' | 'style' | 'batch') {
  activePopover.value = activePopover.value === name ? null : name;
}

function selectModel(id: string) {
  props.card.model = id;
  activePopover.value = null;
  canvas.scheduleSave();
}

function onEditorBlur() {
  window.setTimeout(() => {
    mentionOpen.value = false;
  }, 160);
}

const estimatedPrice = computed(() => {
  const res = (props.card.resolution || '720p').toLowerCase();
  const dur = Math.max(4, Number(props.card.duration || 15));
  const batch = Math.max(1, Number(props.card.batchCount || 1));
  const modelId = props.card.model || 'seedance-2.0-mini';
  const hasVideoRef = refItems.value.some((r) => r.kind === 'video');

  const modelMul: Record<string, number> = {
    'seedance-2.0-mini': 1.0,
    'seedance-2.0-fast': 1.15,
    'seedance-2.0': 1.5,
    'seedance-2.5-flash': 1.25,
    'seedance-2.5': 1.8,
  };
  const tier = modelMul[modelId] ?? 1.0;

  if (activeProvider.value === 'tensor') {
    return {
      text: 'Tensor Energy',
      detail: `Tensor (Forge) 渠道按账号 Energy 实际消耗计费（${currentModelLabel.value} · ${res} ${dur}秒 × ${batch}）`,
    };
  }

  if (activeProvider.value === 'runninghub') {
    // Baseline calibrated against live RunningHub sparkvideo-2.0-mini billing (5s multimodal w/ video = ¥4.00)
    const baseRate = hasVideoRef
      ? res === '1080p'
        ? 1.0
        : 0.8
      : res === '480p'
        ? 0.3
        : res === '1080p'
          ? 0.8
          : 0.6;
    const rate = baseRate * tier;
    const total = rate * dur * batch;
    const coins = Math.round(total * 100);
    return {
      text: `~¥${total.toFixed(2)}`,
      detail: `RunningHub 参考预估：约 ¥${total.toFixed(2)}（约 ${coins} 积分 · ${currentModelLabel.value} · ${res} ${dur}秒 × ${batch}；最终以 RunningHub 返回实扣为准）`,
    };
  }

  // Volcengine Ark official Seedance token-equivalent estimate
  const audioMul = props.card.generateAudio === false ? 0.85 : 1.0;
  const baseByRes: Record<string, number> = {
    '480p': 0.35,
    '720p': 0.55,
    '1080p': 0.95,
  };
  const rate = (baseByRes[res] ?? 0.55) * tier;
  const total = rate * dur * audioMul * batch;
  return {
    text: `~¥${total.toFixed(2)}`,
    detail: `火山方舟参考预估：约 ¥${total.toFixed(2)}（${currentModelLabel.value} · ${res} · ${dur}秒 × ${batch}；最终按官方实际 Token 计费）`,
  };
});

function handleSubmit() {
  activePopover.value = null;
  void canvas.runSingleNode(props.card.id);
}
</script>

<template>
  <div
    class="composer-host"
    :class="{ expanded }"
    @pointerdown.stop
    @click="activePopover = null"
  >
    <div class="composer-card" @click.stop>
      <!-- Top Header: Mode Tabs + Smart Reference + Expand -->
      <div class="composer-head">
        <div class="mode-tabs">
          <button
            v-for="m in modes"
            :key="m.id"
            type="button"
            class="mode-tab"
            :class="{ active: currentMode === m.id }"
            @click="currentMode = m.id"
          >
            {{ m.label }}
          </button>
        </div>

        <div class="head-actions">
          <button type="button" class="smart-ref-btn" @click="handleSmartReference">
            <Wand2 :size="12" />
            智能引用
          </button>
          <button
            type="button"
            class="expand-btn"
            :title="expanded ? '收起' : '放大查看'"
            @click="expanded = !expanded"
          >
            <Minimize2 v-if="expanded" :size="14" />
            <Maximize2 v-else :size="14" />
          </button>
        </div>
      </div>

      <!-- Reference Asset Strip (Omni / FirstFrame / FirstLastFrame) -->
      <div v-if="currentMode !== 't2v'" class="ref-strip">
        <div
          v-for="item in refItems"
          :key="item.key"
          class="ref-chip"
          :class="item.kind"
          :title="`点击插入 ${item.tag}（${item.nodeName}）`"
          @click="insertMentionTag(item.tag)"
        >
          <template v-if="item.kind === 'audio'">
            <div class="audio-chip-bg">
              <div class="mini-waves">
                <i /><i /><i /><i /><i />
              </div>
            </div>
          </template>
          <template v-else-if="item.kind === 'video'">
            <video
              v-if="item.thumbUrl && isVideoUrl(item.thumbUrl)"
              :src="item.thumbUrl"
              muted
              playsinline
              preload="metadata"
              class="chip-img"
            />
            <img v-else-if="item.thumbUrl" :src="item.thumbUrl" :alt="item.nodeName" class="chip-img" />
            <div v-else class="chip-fallback"><Film :size="15" /></div>
            <span class="chip-play"><Play :size="10" fill="currentColor" /></span>
          </template>
          <template v-else>
            <img v-if="item.thumbUrl" :src="item.thumbUrl" :alt="item.nodeName" class="chip-img" />
            <div v-else class="chip-fallback"><ImageIcon :size="15" /></div>
          </template>

          <span class="chip-badge">{{ item.kind === 'audio' ? `♪${item.label}` : item.label }}</span>
          <button
            type="button"
            class="chip-remove"
            title="移除引用"
            @click.stop="detachRef(item)"
          >
            <X :size="10" />
          </button>
        </div>

        <button
          type="button"
          class="add-ref-btn"
          title="从画布选择或本地上传参考素材"
          @click="pickerOpen = true"
        >
          <Plus :size="17" />
        </button>
      </div>

      <!-- Prompt Editor with @ Mention Chips -->
      <div class="editor-wrap">
        <div
          ref="editorRef"
          class="mention-editor"
          contenteditable="true"
          spellcheck="false"
          data-placeholder="描述你想要生成的内容，输入 @ 可快速引用已连接的图片、视频或音频…"
          @input="onEditorInput"
          @keydown="onEditorKeydown"
          @blur="onEditorBlur"
        />

        <!-- @ Mention Autocomplete Popup -->
        <div v-if="mentionOpen && filteredMentions.length" class="mention-popup">
          <div class="mention-popup-title">已连接节点 · 点击或回车插入</div>
          <button
            v-for="(item, idx) in filteredMentions"
            :key="item.key"
            type="button"
            class="mention-option"
            :class="{ active: idx === mentionIndex }"
            @mousedown.prevent="insertMentionTag(item.tag, true)"
          >
            <span class="opt-thumb" :class="item.kind">
              <video
                v-if="item.kind === 'video' && item.thumbUrl && isVideoUrl(item.thumbUrl)"
                :src="item.thumbUrl"
                muted
                playsinline
                preload="metadata"
              />
              <img v-else-if="item.kind !== 'audio' && item.thumbUrl && !isVideoUrl(item.thumbUrl)" :src="item.thumbUrl" alt="" />
              <Music v-else-if="item.kind === 'audio'" :size="13" />
              <Film v-else-if="item.kind === 'video'" :size="13" />
              <ImageIcon v-else :size="13" />
            </span>
            <span class="opt-label">{{ item.label }}</span>
            <span class="opt-name">（{{ item.nodeName }}）</span>
          </button>
        </div>
      </div>

      <!-- Bottom Control Bar -->
      <div class="composer-foot">
        <div class="foot-left">
          <!-- Model Selector -->
          <div class="ctrl-anchor">
            <button type="button" class="ctrl-btn" @click.stop="togglePopover('model')">
              <Sparkles :size="13" class="lime-dot" />
              <span class="ch-badge">{{ providerShortLabel }}</span>
              <span>{{ currentModelLabel }}</span>
              <ChevronDown :size="13" />
            </button>

            <div v-if="activePopover === 'model'" class="ctrl-popover model-pop" @click.stop>
              <div class="pop-head">选择渠道与模型</div>
              <div class="pop-channel-tabs">
                <button
                  v-for="tab in providerTabs"
                  :key="tab.id"
                  type="button"
                  class="pop-ch-btn"
                  :class="{ active: activeProvider === tab.id }"
                  @click="switchChannel(tab.id)"
                >
                  {{ tab.label }}
                </button>
              </div>
              <button
                v-for="m in filteredModels"
                :key="m.id"
                type="button"
                class="model-row"
                :class="{ active: card.model === m.id }"
                @click="selectModel(m.id)"
              >
                <span>{{ m.label }}</span>
                <Check v-if="card.model === m.id" :size="13" class="lime-dot" />
              </button>
            </div>
          </div>

          <!-- Params Pill (720p / 15秒 / 9:16) -->
          <div class="ctrl-anchor">
            <button type="button" class="ctrl-btn" @click.stop="togglePopover('params')">
              <span>{{ card.resolution || '720p' }} / {{ card.duration || 15 }}秒 / {{ card.aspect || '16:9' }}</span>
              <ChevronDown :size="13" />
            </button>

            <div v-if="activePopover === 'params'" class="ctrl-popover params-pop" @click.stop>
              <div class="param-block">
                <label>分辨率</label>
                <div class="seg-row">
                  <button
                    v-for="r in resolutionOptions"
                    :key="r"
                    type="button"
                    class="seg-btn"
                    :class="{ active: (card.resolution || '720p') === r }"
                    @click="
                      card.resolution = r;
                      canvas.scheduleSave();
                    "
                  >
                    {{ r }}
                  </button>
                </div>
              </div>

              <div class="param-block">
                <div class="param-label-row">
                  <label>生成时长</label>
                  <span class="dur-pill">{{ card.duration || 15 }} 秒</span>
                </div>
                <input
                  type="range"
                  min="5"
                  max="15"
                  step="1"
                  :value="card.duration || 15"
                  class="dur-slider"
                  @input="
                    card.duration = Number(($event.target as HTMLInputElement).value);
                    canvas.scheduleSave();
                  "
                />
              </div>

              <div class="param-block">
                <label>生成视频音频</label>
                <div class="seg-row">
                  <button
                    type="button"
                    class="seg-btn"
                    :class="{ active: card.generateAudio !== false }"
                    @click="
                      card.generateAudio = true;
                      canvas.scheduleSave();
                    "
                  >
                    是
                  </button>
                  <button
                    type="button"
                    class="seg-btn"
                    :class="{ active: card.generateAudio === false }"
                    @click="
                      card.generateAudio = false;
                      canvas.scheduleSave();
                    "
                  >
                    否
                  </button>
                </div>
              </div>

              <div class="param-block">
                <label>比例</label>
                <div class="aspect-grid">
                  <button
                    v-for="a in aspectOptions"
                    :key="a"
                    type="button"
                    class="aspect-btn"
                    :class="{ active: (card.aspect || '16:9') === a }"
                    @click="
                      card.aspect = a;
                      canvas.scheduleSave();
                    "
                  >
                    {{ a }}
                  </button>
                </div>
              </div>
            </div>
          </div>

          <!-- Style Selector -->
          <div class="ctrl-anchor">
            <button type="button" class="ctrl-btn" @click.stop="togglePopover('style')">
              <Palette :size="13" />
              <span>{{ card.stylePreset && card.stylePreset !== '无' ? card.stylePreset : '风格' }}</span>
            </button>

            <div v-if="activePopover === 'style'" class="ctrl-popover style-pop" @click.stop>
              <div class="pop-head">画面风格</div>
              <div class="style-grid">
                <button
                  v-for="s in styleOptions"
                  :key="s"
                  type="button"
                  class="style-chip"
                  :class="{ active: (card.stylePreset || '无') === s }"
                  @click="
                    card.stylePreset = s;
                    activePopover = null;
                    canvas.scheduleSave();
                  "
                >
                  {{ s }}
                </button>
              </div>
            </div>
          </div>
        </div>

        <div class="foot-right">
          <!-- Batch 1x / 2x -->
          <div class="ctrl-anchor">
            <button type="button" class="ctrl-btn compact" @click.stop="togglePopover('batch')">
              <span>{{ card.batchCount || 1 }}x</span>
              <ChevronDown :size="12" />
            </button>

            <div v-if="activePopover === 'batch'" class="ctrl-popover batch-pop" @click.stop>
              <button
                v-for="n in [1, 2, 4]"
                :key="n"
                type="button"
                class="batch-row"
                :class="{ active: (card.batchCount || 1) === n }"
                @click="
                  card.batchCount = n;
                  activePopover = null;
                  canvas.scheduleSave();
                "
              >
                {{ n }}x
              </button>
            </div>
          </div>

          <!-- White Primary Generate Pill with Estimated Price -->
          <button
            type="button"
            class="submit-pill"
            :title="estimatedPrice.detail"
            :disabled="card.status === 'running'"
            @click="handleSubmit"
          >
            <Loader2 v-if="card.status === 'running'" :size="14" class="spin" />
            <template v-else>
              <Sparkles :size="12" class="price-spark" />
              <span class="price-text">{{ estimatedPrice.text }}</span>
              <span class="arrow-circle"><ArrowUp :size="13" :stroke-width="2.6" /></span>
            </template>
          </button>
        </div>
      </div>
    </div>

    <!-- "+" Reference Picker Modal (Canvas Assets + Local Upload) -->
    <div v-if="pickerOpen" class="picker-backdrop" @click="pickerOpen = false">
      <div class="picker-modal" @click.stop>
        <div class="picker-head">
          <div class="picker-title">添加参考素材</div>
          <div class="picker-search-wrap">
            <Search :size="13" class="search-ico" />
            <input v-model="pickerSearch" class="picker-search" placeholder="搜索节点名称…" />
          </div>
          <button type="button" class="picker-close" @click="pickerOpen = false">
            <X :size="16" />
          </button>
        </div>

        <div class="picker-filters">
          <button
            v-for="f in [
              { id: 'all', label: '全部' },
              { id: 'image', label: '图片' },
              { id: 'video', label: '视频' },
              { id: 'audio', label: '音频' },
            ] as const"
            :key="f.id"
            type="button"
            class="filter-pill"
            :class="{ active: pickerFilter === f.id }"
            @click="pickerFilter = f.id"
          >
            {{ f.label }}
          </button>
        </div>

        <div class="picker-grid">
          <!-- Local Upload Card -->
          <button
            type="button"
            class="picker-card upload-card"
            :disabled="uploading"
            @click="uploadInput?.click()"
          >
            <Loader2 v-if="uploading" :size="20" class="spin" />
            <Upload v-else :size="20" />
            <span>{{ uploading ? '上传中…' : '本地上传' }}</span>
            <small>支持图片 / 视频 / 音频</small>
          </button>

          <input
            ref="uploadInput"
            type="file"
            accept="image/*,video/*,audio/*,.mp4,.mov,.webm,.mkv,.m4v,.avi,.mp3,.wav,.m4a,.aac,.ogg,.flac,.opus"
            multiple
            hidden
            @change="handleDirectUpload"
          />

          <!-- Canvas Media Cards -->
          <div
            v-for="cand in canvasMediaCandidates"
            :key="cand.key"
            class="picker-card"
            :class="{ attached: cand.attached }"
            @click="toggleCandidate(cand)"
          >
            <video
              v-if="cand.kind === 'video' && cand.thumbUrl && isVideoUrl(cand.thumbUrl)"
              :src="cand.thumbUrl"
              muted
              playsinline
              preload="metadata"
            />
            <img v-else-if="cand.kind !== 'audio' && cand.thumbUrl" :src="cand.thumbUrl" :alt="cand.name" />
            <div v-else class="picker-fallback">
              <Music v-if="cand.kind === 'audio'" :size="22" />
              <Film v-else-if="cand.kind === 'video'" :size="22" />
              <ImageIcon v-else :size="22" />
            </div>

            <span class="cand-type">{{ cand.kind === 'image' ? '图片' : cand.kind === 'video' ? '视频' : '音频' }}</span>
            <span v-if="cand.attached" class="cand-attached">已引用</span>
            <div class="cand-name">{{ cand.name }}</div>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.composer-host {
  position: fixed;
  left: 50%;
  bottom: 22px;
  transform: translateX(-50%);
  z-index: 60;
  width: min(650px, calc(100vw - 120px));
}

@media (max-width: 1120px) {
  .composer-host:not(.expanded) {
    bottom: 62px;
  }
}

.composer-host.expanded {
  inset: 0;
  width: auto;
  transform: none;
  background: rgba(0, 0, 0, 0.65);
  backdrop-filter: blur(8px);
  display: grid;
  place-items: center;
  padding: 28px;
}

.composer-host.expanded .composer-card {
  width: min(820px, 92vw);
}

.composer-host.expanded .mention-editor {
  min-height: 240px;
  max-height: 46vh;
}

.composer-card {
  background: rgba(26, 26, 28, 0.97);
  border: 1px solid rgba(255, 255, 255, 0.11);
  border-radius: 20px;
  box-shadow: 0 20px 48px rgba(0, 0, 0, 0.58);
  backdrop-filter: blur(24px);
  padding: 13px 16px 12px;
  display: flex;
  flex-direction: column;
  gap: 11px;
}

.composer-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
}

.mode-tabs {
  display: inline-flex;
  align-items: center;
  gap: 4px;
}

.mode-tab {
  border: 1px solid transparent;
  background: transparent;
  color: var(--muted);
  font-size: 12.5px;
  font-weight: 500;
  padding: 5px 11px;
  border-radius: 999px;
  transition: background 0.14s ease, color 0.14s ease, border-color 0.14s ease;
}

.mode-tab:hover {
  color: rgba(255, 255, 255, 0.85);
}

.mode-tab.active {
  color: #ffffff;
  background: linear-gradient(rgba(255, 255, 255, 0.08), rgba(255, 255, 255, 0.03));
  border-color: rgba(255, 255, 255, 0.12);
}

.head-actions {
  display: flex;
  align-items: center;
  gap: 6px;
}

.smart-ref-btn {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  height: 28px;
  padding: 0 11px;
  border-radius: 8px;
  border: 1px solid var(--line);
  background: rgba(255, 255, 255, 0.04);
  color: rgba(255, 255, 255, 0.86);
  font-size: 12px;
}

.smart-ref-btn:hover {
  border-color: var(--accent-border);
  color: var(--accent);
}

.expand-btn {
  width: 28px;
  height: 28px;
  border-radius: 8px;
  border: 0;
  background: transparent;
  color: var(--muted);
  display: grid;
  place-items: center;
}

.expand-btn:hover {
  background: rgba(255, 255, 255, 0.08);
  color: #ffffff;
}

/* Reference Strip */
.ref-strip {
  display: flex;
  align-items: center;
  gap: 7px;
  overflow-x: auto;
  padding-bottom: 2px;
}

.ref-chip {
  position: relative;
  width: 48px;
  height: 48px;
  flex-shrink: 0;
  border-radius: 10px;
  overflow: hidden;
  border: 1px solid rgba(255, 255, 255, 0.14);
  background: #232326;
  cursor: pointer;
  transition: transform 0.12s ease, border-color 0.14s ease;
}

.ref-chip:hover {
  border-color: var(--accent);
  transform: translateY(-1px);
}

.chip-img {
  width: 100%;
  height: 100%;
  object-fit: cover;
  display: block;
}

.chip-fallback {
  width: 100%;
  height: 100%;
  display: grid;
  place-items: center;
  color: var(--muted);
}

.audio-chip-bg {
  width: 100%;
  height: 100%;
  background: linear-gradient(135deg, #7c3aed, #a855f7);
  display: grid;
  place-items: center;
}

.mini-waves {
  display: flex;
  align-items: center;
  gap: 2.5px;
  height: 18px;
  margin-bottom: 10px;
}

.mini-waves i {
  display: block;
  width: 2.5px;
  border-radius: 2px;
  background: #ffffff;
}

.mini-waves i:nth-child(1) { height: 7px; }
.mini-waves i:nth-child(2) { height: 15px; }
.mini-waves i:nth-child(3) { height: 11px; }
.mini-waves i:nth-child(4) { height: 17px; }
.mini-waves i:nth-child(5) { height: 8px; }

.chip-play {
  position: absolute;
  left: 50%;
  top: 40%;
  transform: translate(-50%, -50%);
  width: 18px;
  height: 18px;
  border-radius: 50%;
  background: rgba(0, 0, 0, 0.65);
  color: #ffffff;
  display: grid;
  place-items: center;
}

.chip-badge {
  position: absolute;
  left: 2px;
  right: 2px;
  bottom: 2px;
  text-align: center;
  font-size: 9.5px;
  line-height: 14px;
  border-radius: 5px;
  background: rgba(12, 12, 14, 0.78);
  color: #ffffff;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.chip-remove {
  position: absolute;
  top: 2px;
  right: 2px;
  width: 15px;
  height: 15px;
  border-radius: 50%;
  border: 0;
  background: rgba(0, 0, 0, 0.72);
  color: #ffffff;
  display: grid;
  place-items: center;
  opacity: 0;
  transition: opacity 0.12s ease;
}

.ref-chip:hover .chip-remove {
  opacity: 1;
}

.add-ref-btn {
  width: 48px;
  height: 48px;
  flex-shrink: 0;
  border-radius: 10px;
  border: 1px dashed rgba(255, 255, 255, 0.24);
  background: rgba(255, 255, 255, 0.03);
  color: var(--text-secondary);
  display: grid;
  place-items: center;
  transition: border-color 0.14s ease, color 0.14s ease, background 0.14s ease;
}

.add-ref-btn:hover {
  border-color: var(--accent);
  color: var(--accent);
  background: rgba(204, 255, 0, 0.06);
}

/* Mention Prompt Editor */
.editor-wrap {
  position: relative;
}

.mention-editor {
  min-height: 112px;
  max-height: 185px;
  overflow-y: auto;
  outline: none;
  font-size: 13.5px;
  line-height: 1.65;
  color: rgba(255, 255, 255, 0.9);
  white-space: pre-wrap;
  word-break: break-word;
  user-select: text;
}

.mention-editor:empty::before {
  content: attr(data-placeholder);
  color: var(--muted);
  pointer-events: none;
}

.mention-editor :deep(.mention-chip) {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  color: var(--accent);
  font-weight: 600;
  padding: 0 3px;
  margin: 0 1px;
  border-radius: 5px;
  background: rgba(204, 255, 0, 0.09);
  vertical-align: baseline;
}

.mention-editor :deep(.m-thumb) {
  width: 15px;
  height: 15px;
  border-radius: 3px;
  object-fit: cover;
  display: inline-grid;
  place-items: center;
  font-size: 9px;
  vertical-align: -2px;
}

.mention-editor :deep(.m-thumb-audio) {
  background: #8b5cf6;
  color: #ffffff;
}

.mention-editor :deep(.m-thumb-generic) {
  background: rgba(255, 255, 255, 0.14);
  color: #ffffff;
}

/* @ Autocomplete Popup */
.mention-popup {
  position: absolute;
  left: 0;
  bottom: calc(100% + 8px);
  width: 280px;
  max-height: 220px;
  overflow-y: auto;
  padding: 6px;
  border-radius: 14px;
  background: rgba(24, 24, 26, 0.98);
  border: 1px solid var(--line-strong);
  box-shadow: var(--shadow);
  z-index: 80;
}

.mention-popup-title {
  padding: 4px 8px 6px;
  font-size: 11px;
  color: var(--muted);
}

.mention-option {
  width: 100%;
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 6px 8px;
  border: 0;
  border-radius: 9px;
  background: transparent;
  color: var(--text);
  font-size: 12.5px;
  text-align: left;
}

.mention-option.active,
.mention-option:hover {
  background: rgba(255, 255, 255, 0.09);
}

.opt-thumb {
  width: 26px;
  height: 26px;
  border-radius: 6px;
  overflow: hidden;
  flex-shrink: 0;
  background: #2c2c30;
  display: grid;
  place-items: center;
}

.opt-thumb.audio {
  background: linear-gradient(135deg, #7c3aed, #a855f7);
}

.opt-thumb img {
  width: 100%;
  height: 100%;
  object-fit: cover;
}

.opt-label {
  color: var(--accent);
  font-weight: 600;
}

.opt-name {
  color: var(--muted);
  font-size: 11.5px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* Live Progress Strip */
.gen-progress-strip {
  display: grid;
  gap: 6px;
  padding: 8px 10px;
  border-radius: 12px;
  background: rgba(204, 255, 0, 0.07);
  border: 1px solid var(--accent-border);
}

.gen-progress-meta {
  display: flex;
  align-items: center;
  justify-content: space-between;
  font-size: 11.5px;
  color: rgba(255, 255, 255, 0.9);
}

.gen-progress-meta span {
  display: inline-flex;
  align-items: center;
  gap: 6px;
}

.gen-progress-right {
  display: flex;
  align-items: center;
  gap: 8px;
}

.stop-btn {
  border: 1px solid rgba(255, 107, 107, 0.5);
  background: rgba(255, 107, 107, 0.16);
  color: #ffb8b8;
  border-radius: 999px;
  padding: 2px 10px;
  font-size: 11px;
  font-weight: 600;
}

.stop-btn:hover {
  background: rgba(255, 107, 107, 0.3);
  color: #ffffff;
}

.gen-progress-meta strong {
  color: var(--accent);
  font-variant-numeric: tabular-nums;
}

.gen-progress-track {
  height: 4px;
  border-radius: 999px;
  background: rgba(255, 255, 255, 0.12);
  overflow: hidden;
}

.gen-progress-fill {
  width: 38%;
  height: 100%;
  border-radius: 999px;
  background: var(--accent);
  animation: progressSlide 1.35s ease-in-out infinite;
}

@keyframes progressSlide {
  0% {
    transform: translateX(-100%);
  }
  100% {
    transform: translateX(280%);
  }
}

/* Bottom Controls */
.composer-foot {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  padding-top: 8px;
  border-top: 1px solid rgba(255, 255, 255, 0.07);
}

.foot-left,
.foot-right {
  display: flex;
  align-items: center;
  gap: 6px;
}

.ctrl-anchor {
  position: relative;
}

.ctrl-btn {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  height: 30px;
  padding: 0 10px;
  border-radius: 999px;
  border: 1px solid transparent;
  background: transparent;
  color: rgba(255, 255, 255, 0.84);
  font-size: 12px;
  white-space: nowrap;
  flex-shrink: 0;
}

.ctrl-btn:hover {
  background: rgba(255, 255, 255, 0.07);
  border-color: var(--line);
}

.ctrl-btn.compact {
  padding: 0 8px;
}

.lime-dot {
  color: var(--accent);
}

.ctrl-popover {
  position: absolute;
  bottom: calc(100% + 10px);
  left: 0;
  border-radius: 16px;
  background: #1c1c1e;
  border: 1px solid var(--line-strong);
  box-shadow: var(--shadow);
  padding: 10px;
  z-index: 90;
}

.pop-head {
  font-size: 11px;
  color: var(--muted);
  padding: 2px 6px 8px;
}

.ch-badge {
  display: inline-flex;
  align-items: center;
  padding: 1px 6px;
  border-radius: 5px;
  background: rgba(204, 255, 0, 0.14);
  color: var(--accent);
  font-size: 10.5px;
  font-weight: 600;
  line-height: 1.4;
}

.model-pop {
  width: 248px;
  display: flex;
  flex-direction: column;
  gap: 3px;
}

.pop-channel-tabs {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 4px;
  padding: 3px;
  margin-bottom: 4px;
  border-radius: 9px;
  background: rgba(255, 255, 255, 0.04);
  border: 1px solid var(--line);
}

.pop-ch-btn {
  border: 1px solid transparent;
  border-radius: 7px;
  padding: 5px 4px;
  background: transparent;
  color: var(--muted);
  font-size: 11px;
  font-weight: 500;
  cursor: pointer;
}

.pop-ch-btn.active {
  background: var(--surface-selected);
  border-color: var(--accent-border);
  color: var(--accent);
  font-weight: 600;
}

.model-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 7px 9px;
  border: 0;
  border-radius: 9px;
  background: transparent;
  font-size: 12.5px;
  text-align: left;
}

.model-row:hover,
.model-row.active {
  background: var(--surface-selected);
}

.params-pop {
  width: 290px;
  display: flex;
  flex-direction: column;
  gap: 12px;
  padding: 14px;
}

.param-block {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.param-block label {
  font-size: 11.5px;
  color: var(--muted);
}

.param-label-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.dur-pill {
  padding: 1px 8px;
  border-radius: 999px;
  background: var(--surface-selected);
  font-size: 11.5px;
}

.dur-slider {
  width: 100%;
  accent-color: var(--accent);
}

.seg-row {
  display: grid;
  grid-auto-flow: column;
  grid-auto-columns: 1fr;
  gap: 4px;
  padding: 3px;
  border-radius: 10px;
  background: var(--surface-group);
}

.seg-btn {
  border: 0;
  border-radius: 7px;
  padding: 5px 0;
  background: transparent;
  color: var(--muted);
  font-size: 12px;
}

.seg-btn.active {
  background: var(--surface-selected);
  color: #ffffff;
  font-weight: 600;
}

.aspect-grid {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 5px;
}

.aspect-btn {
  border: 1px solid var(--line);
  border-radius: 8px;
  padding: 5px 0;
  background: var(--surface-group);
  color: var(--muted);
  font-size: 11.5px;
}

.aspect-btn.active {
  border-color: var(--accent);
  color: var(--accent);
  background: var(--accent-soft);
}

.style-pop {
  width: 240px;
}

.style-grid {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.style-chip {
  border: 1px solid var(--line);
  border-radius: 999px;
  padding: 4px 10px;
  background: var(--surface-group);
  color: var(--text-secondary);
  font-size: 12px;
}

.style-chip.active {
  border-color: var(--accent);
  color: var(--accent);
  background: var(--accent-soft);
}

.batch-pop {
  right: 0;
  left: auto;
  width: 76px;
  padding: 5px;
  display: grid;
  gap: 2px;
}

.batch-row {
  border: 0;
  border-radius: 8px;
  padding: 6px 0;
  background: transparent;
  font-size: 12.5px;
}

.batch-row:hover {
  background: rgba(255, 255, 255, 0.08);
}

.batch-row.active {
  color: var(--accent);
  font-weight: 700;
}

.submit-pill {
  display: inline-flex;
  align-items: center;
  gap: 7px;
  height: 32px;
  padding: 0 5px 0 13px;
  border-radius: 999px;
  border: 0;
  background: var(--action-surface);
  color: var(--action-ink);
  font-size: 12.5px;
  font-weight: 700;
  white-space: nowrap;
  flex-shrink: 0;
}

.submit-pill:hover:not(:disabled) {
  background: #e8e8e8;
}

.submit-pill:disabled {
  opacity: 0.6;
  padding: 0 14px;
}

.price-spark {
  color: #171719;
  opacity: 0.75;
}

.price-text {
  font-variant-numeric: tabular-nums;
  letter-spacing: -0.01em;
}

.arrow-circle {
  width: 24px;
  height: 24px;
  border-radius: 50%;
  background: #171719;
  color: #ffffff;
  display: grid;
  place-items: center;
}

/* Reference Picker Modal */
.picker-backdrop {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.62);
  backdrop-filter: blur(6px);
  z-index: 200;
  display: grid;
  place-items: center;
}

.picker-modal {
  width: min(680px, 92vw);
  max-height: 78vh;
  background: #1c1c1e;
  border: 1px solid var(--line-strong);
  border-radius: 20px;
  box-shadow: var(--shadow);
  padding: 16px 18px;
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.picker-head {
  display: flex;
  align-items: center;
  gap: 12px;
}

.picker-title {
  font-size: 15px;
  font-weight: 600;
  flex: 1;
}

.picker-search-wrap {
  position: relative;
  width: 210px;
}

.search-ico {
  position: absolute;
  left: 10px;
  top: 50%;
  transform: translateY(-50%);
  color: var(--muted);
}

.picker-search {
  width: 100%;
  height: 32px;
  padding: 0 10px 0 28px;
  border-radius: 999px;
  border: 1px solid var(--line);
  background: var(--surface-group);
  outline: none;
  font-size: 12px;
}

.picker-close {
  width: 30px;
  height: 30px;
  border-radius: 50%;
  border: 0;
  background: rgba(255, 255, 255, 0.06);
  color: var(--text-secondary);
  display: grid;
  place-items: center;
}

.picker-filters {
  display: flex;
  gap: 6px;
}

.filter-pill {
  padding: 4px 12px;
  border-radius: 999px;
  border: 1px solid var(--line);
  background: var(--surface-group);
  color: var(--muted);
  font-size: 12px;
}

.filter-pill.active {
  border-color: var(--accent);
  color: var(--accent);
  background: var(--accent-soft);
}

.picker-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(126px, 1fr));
  gap: 10px;
  overflow-y: auto;
  padding: 2px;
}

.picker-card {
  position: relative;
  height: 146px;
  border-radius: 12px;
  overflow: hidden;
  border: 1px solid var(--line);
  background: #141416;
  cursor: pointer;
}

.picker-card.attached {
  border-color: var(--accent);
}

.picker-card img,
.picker-card video {
  width: 100%;
  height: 100%;
  object-fit: cover;
}

.upload-card {
  border-style: dashed;
  border-color: rgba(255, 255, 255, 0.22);
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 6px;
  color: var(--text-secondary);
  font-size: 12.5px;
}

.upload-card small {
  font-size: 10.5px;
  color: var(--muted);
}

.upload-card:hover {
  border-color: var(--accent);
  color: var(--accent);
}

.picker-fallback {
  width: 100%;
  height: 100%;
  display: grid;
  place-items: center;
  color: var(--muted);
}

.cand-type {
  position: absolute;
  top: 6px;
  left: 6px;
  padding: 1px 6px;
  border-radius: 5px;
  background: rgba(0, 0, 0, 0.65);
  font-size: 10px;
}

.cand-attached {
  position: absolute;
  top: 6px;
  right: 6px;
  padding: 1px 6px;
  border-radius: 5px;
  background: var(--accent);
  color: #11130a;
  font-size: 10px;
  font-weight: 700;
}

.cand-name {
  position: absolute;
  left: 0;
  right: 0;
  bottom: 0;
  padding: 16px 8px 6px;
  background: linear-gradient(transparent, rgba(0, 0, 0, 0.85));
  font-size: 11px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.spin {
  animation: spin 1s linear infinite;
}

@keyframes spin {
  to {
    transform: rotate(360deg);
  }
}
</style>
