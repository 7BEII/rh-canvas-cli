<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue';
import {
  Play,
  Ungroup,
  Eye,
  EyeOff,
  RotateCcw,
  LayoutGrid,
  Map as MapIcon,
} from 'lucide-vue-next';
import CanvasNode from '@/components/CanvasNode.vue';
import VideoComposerPanel from '@/components/VideoComposerPanel.vue';
import { NODE_DEFS } from '@/data/nodeDefs';
import { useCanvasStore } from '@/stores/canvas';
import type { CanvasCard, NodeType, PortKind } from '@/types/canvas';

const canvas = useCanvasStore();

const boardRef = ref<HTMLElement | null>(null);
const isPanning = ref(false);
const isDragging = ref(false);
const spacePressed = ref(false);
const showEdges = ref(true);
const showMinimap = ref(false);

const marquee = ref<{ x1: number; y1: number; x2: number; y2: number } | null>(null);
const connecting = ref<{
  cardId: string;
  kind: PortKind;
  x: number;
  y: number;
} | null>(null);

const quickMenu = ref<{
  screenX: number;
  screenY: number;
  worldX: number;
  worldY: number;
} | null>(null);

const activeVideoCard = computed<CanvasCard | null>(() => {
  const selected = canvas.selectedCards;
  if (selected.length === 1 && selected[0].type === 'video') return selected[0];
  return null;
});

function screenToWorld(clientX: number, clientY: number) {
  const rect = boardRef.value!.getBoundingClientRect();
  return {
    x: (clientX - rect.left - canvas.viewport.x) / canvas.viewport.z,
    y: (clientY - rect.top - canvas.viewport.y) / canvas.viewport.z,
  };
}

function getCardBox(card: CanvasCard) {
  const def = NODE_DEFS[card.type] || NODE_DEFS.prompt;
  const w = card.width || (card.type === 'video' ? 210 : card.type === 'source' ? 176 : def.defaultWidth || 260);
  const h =
    card.height ||
    (card.type === 'video'
      ? 280
      : card.type === 'source'
        ? card.mediaKind === 'audio'
          ? 112
          : 210
        : 180);
  return { w, h };
}

function getPortPosition(card: CanvasCard, side: 'in' | 'out') {
  const { w, h } = getCardBox(card);
  return {
    x: side === 'in' ? card.x : card.x + w,
    y: card.y + h / 2,
  };
}

function bezierPath(x1: number, y1: number, x2: number, y2: number) {
  const dx = Math.max(64, Math.abs(x2 - x1) * 0.45);
  return `M ${x1} ${y1} C ${x1 + dx} ${y1}, ${x2 - dx} ${y2}, ${x2} ${y2}`;
}

const edges = computed(() => {
  if (!showEdges.value) return [];
  const list: Array<{
    key: string;
    from: string;
    to: string;
    kind: PortKind;
    path: string;
    active: boolean;
  }> = [];
  for (const target of canvas.cards) {
    for (const refItem of target.refs || []) {
      const source = canvas.cards.find((c) => c.id === refItem.from);
      if (!source) continue;
      const p1 = getPortPosition(source, 'out');
      const p2 = getPortPosition(target, 'in');
      list.push({
        key: `${source.id}->${target.id}:${refItem.kind}`,
        from: source.id,
        to: target.id,
        kind: refItem.kind,
        path: bezierPath(p1.x, p1.y, p2.x, p2.y),
        active: Boolean(source.selected || target.selected),
      });
    }
  }
  return list;
});

// Focus highlighting: when cards are selected, emphasise their direct upstream / downstream
// neighbours and dim everything else, so it's obvious which assets are actually wired in.
const focus = computed(() => {
  const selectedIds = new Set(canvas.cards.filter((c) => c.selected).map((c) => c.id));
  const related = new Set<string>(selectedIds);
  if (selectedIds.size) {
    for (const card of canvas.cards) {
      for (const r of card.refs || []) {
        if (selectedIds.has(card.id)) related.add(r.from);
        if (selectedIds.has(r.from)) related.add(card.id);
      }
    }
  }
  return { active: selectedIds.size > 0 && related.size > selectedIds.size, selectedIds, related };
});

function nodeFocusClass(card: CanvasCard) {
  const f = focus.value;
  if (!f.active || f.selectedIds.has(card.id)) return {};
  return { 'focus-related': f.related.has(card.id), 'focus-dimmed': !f.related.has(card.id) };
}

const activeEdges = computed(() => edges.value.filter((e) => e.active));

// Badges like "@图片3" on upstream cards of the single selected video node (same numbering as the composer panel)
const refBadges = computed(() => {
  const target = activeVideoCard.value;
  if (!target) return [];
  const counters = { image: 0, video: 0, audio: 0 } as Record<string, number>;
  const prefix: Record<string, string> = { image: '图片', video: '视频', audio: '音频' };
  const byCard = new Map<string, string[]>();
  for (const r of target.refs || []) {
    if (!(r.kind in counters)) continue;
    if (!canvas.cards.some((c) => c.id === r.from)) continue;
    counters[r.kind] += 1;
    const list = byCard.get(r.from) || [];
    list.push(`@${prefix[r.kind]}${counters[r.kind]}`);
    byCard.set(r.from, list);
  }
  const out: Array<{ id: string; x: number; y: number; text: string }> = [];
  for (const [id, tags] of byCard) {
    const card = canvas.cards.find((c) => c.id === id)!;
    out.push({ id, x: card.x + 6, y: card.y + 6, text: tags.join(' ') });
  }
  return out;
});

const draftEdgePath = computed(() => {
  if (!connecting.value) return '';
  const src = canvas.cards.find((c) => c.id === connecting.value!.cardId);
  if (!src) return '';
  const p1 = getPortPosition(src, 'out');
  return bezierPath(p1.x, p1.y, connecting.value.x, connecting.value.y);
});

const groupBoxes = computed(() =>
  canvas.groups.map((g) => {
    const minX = Math.min(...g.cards.map((c) => c.x)) - 22;
    const minY = Math.min(...g.cards.map((c) => c.y)) - 48;
    const maxX = Math.max(...g.cards.map((c) => c.x + getCardBox(c).w)) + 22;
    const maxY = Math.max(...g.cards.map((c) => c.y + getCardBox(c).h)) + 22;
    return {
      ...g,
      x: minX,
      y: minY,
      w: Math.max(240, maxX - minX),
      h: Math.max(160, maxY - minY),
    };
  }),
);

const minimap = computed(() => {
  if (!canvas.cards.length) return { nodes: [], scale: 1 };
  const minX = Math.min(...canvas.cards.map((c) => c.x)) - 120;
  const minY = Math.min(...canvas.cards.map((c) => c.y)) - 120;
  const maxX = Math.max(...canvas.cards.map((c) => c.x + getCardBox(c).w)) + 120;
  const maxY = Math.max(...canvas.cards.map((c) => c.y + getCardBox(c).h)) + 120;
  const spanX = Math.max(600, maxX - minX);
  const spanY = Math.max(400, maxY - minY);
  const scale = Math.min(168 / spanX, 94 / spanY);
  return {
    scale,
    nodes: canvas.cards.map((c) => {
      const box = getCardBox(c);
      return {
        id: c.id,
        x: (c.x - minX) * scale,
        y: (c.y - minY) * scale,
        w: Math.max(6, box.w * scale),
        h: Math.max(5, box.h * scale),
        selected: !!c.selected,
      };
    }),
  };
});

function onBoardPointerDown(event: PointerEvent) {
  quickMenu.value = null;
  if (event.button === 1 || spacePressed.value || (event.button === 0 && !event.ctrlKey && !event.metaKey)) {
    if (!event.ctrlKey && !event.metaKey && event.button === 0) {
      canvas.selectOnly(null);
    }
    isPanning.value = true;
    const startX = event.clientX;
    const startY = event.clientY;
    const origX = canvas.viewport.x;
    const origY = canvas.viewport.y;

    const onMove = (e: PointerEvent) => {
      canvas.viewport.x = origX + (e.clientX - startX);
      canvas.viewport.y = origY + (e.clientY - startY);
    };
    const onUp = () => {
      isPanning.value = false;
      window.removeEventListener('pointermove', onMove);
      window.removeEventListener('pointerup', onUp);
      canvas.scheduleSave();
    };
    window.addEventListener('pointermove', onMove);
    window.addEventListener('pointerup', onUp);
    return;
  }

  if (event.button === 0 && (event.ctrlKey || event.metaKey)) {
    const rect = boardRef.value!.getBoundingClientRect();
    const x1 = event.clientX - rect.left;
    const y1 = event.clientY - rect.top;
    marquee.value = { x1, y1, x2: x1, y2: y1 };

    const onMove = (e: PointerEvent) => {
      marquee.value = { x1, y1, x2: e.clientX - rect.left, y2: e.clientY - rect.top };
    };
    const onUp = () => {
      if (marquee.value) {
        const left = Math.min(marquee.value.x1, marquee.value.x2);
        const right = Math.max(marquee.value.x1, marquee.value.x2);
        const top = Math.min(marquee.value.y1, marquee.value.y2);
        const bottom = Math.max(marquee.value.y1, marquee.value.y2);
        const wl = (left - canvas.viewport.x) / canvas.viewport.z;
        const wr = (right - canvas.viewport.x) / canvas.viewport.z;
        const wt = (top - canvas.viewport.y) / canvas.viewport.z;
        const wb = (bottom - canvas.viewport.y) / canvas.viewport.z;
        for (const c of canvas.cards) {
          const box = getCardBox(c);
          c.selected = c.x + box.w >= wl && c.x <= wr && c.y + box.h >= wt && c.y <= wb;
        }
      }
      marquee.value = null;
      window.removeEventListener('pointermove', onMove);
      window.removeEventListener('pointerup', onUp);
    };
    window.addEventListener('pointermove', onMove);
    window.addEventListener('pointerup', onUp);
  }
}

function onWheel(event: WheelEvent) {
  const rect = boardRef.value!.getBoundingClientRect();
  const mouseX = event.clientX - rect.left;
  const mouseY = event.clientY - rect.top;
  const factor = event.deltaY < 0 ? 1.09 : 0.91;
  const oldZ = canvas.viewport.z;
  const newZ = Math.max(0.2, Math.min(2.2, Number((oldZ * factor).toFixed(3))));
  const scaleRatio = newZ / oldZ;
  canvas.viewport.x = mouseX - (mouseX - canvas.viewport.x) * scaleRatio;
  canvas.viewport.y = mouseY - (mouseY - canvas.viewport.y) * scaleRatio;
  canvas.viewport.z = newZ;
}

function onNodeDragStart(event: PointerEvent, card: CanvasCard) {
  if (event.button !== 0) return;
  const wasSelected = Boolean(card.selected);
  if (!wasSelected) {
    canvas.selectOnly(card.id);
    // 未选中节点按下时先不弹出底部面板，避免准备拖拽时闪现遮挡，松手后再显示
    isDragging.value = true;
  }
  const targets = canvas.selectedCards.length ? canvas.selectedCards : [card];
  const origins = targets.map((c) => ({ card: c, x: c.x, y: c.y }));
  const startX = event.clientX;
  const startY = event.clientY;
  let moved = false;

  const cleanup = () => {
    isDragging.value = false;
    window.removeEventListener('pointermove', onMove);
    window.removeEventListener('pointerup', onUp, true);
    window.removeEventListener('pointercancel', onUp, true);
    window.removeEventListener('blur', onUp);
  };

  const onMove = (e: PointerEvent) => {
    if (e.buttons === 0) {
      onUp();
      return;
    }
    const dx = (e.clientX - startX) / canvas.viewport.z;
    const dy = (e.clientY - startY) / canvas.viewport.z;
    if (!moved && (Math.abs(dx) > 2 || Math.abs(dy) > 2)) {
      moved = true;
      isDragging.value = true;
      canvas.pushHistory();
    }
    if (moved) {
      for (const item of origins) {
        item.card.x = Math.round(item.x + dx);
        item.card.y = Math.round(item.y + dy);
      }
    }
  };
  const onUp = () => {
    cleanup();
    if (moved) canvas.scheduleSave();
  };
  window.addEventListener('pointermove', onMove);
  window.addEventListener('pointerup', onUp, true);
  window.addEventListener('pointercancel', onUp, true);
  window.addEventListener('blur', onUp);
}

function onGroupDragStart(event: PointerEvent, groupId: string) {
  if (event.button !== 0) return;
  const members = canvas.cards.filter((c) => c.groupId === groupId);
  if (!members.length) return;
  const origins = members.map((c) => ({ card: c, x: c.x, y: c.y }));
  const startX = event.clientX;
  const startY = event.clientY;
  let moved = false;

  const onMove = (e: PointerEvent) => {
    const dx = (e.clientX - startX) / canvas.viewport.z;
    const dy = (e.clientY - startY) / canvas.viewport.z;
    if (!moved && (Math.abs(dx) > 2 || Math.abs(dy) > 2)) {
      moved = true;
      isDragging.value = true;
      canvas.pushHistory();
    }
    if (moved) {
      for (const item of origins) {
        item.card.x = Math.round(item.x + dx);
        item.card.y = Math.round(item.y + dy);
      }
    }
  };
  const onUp = () => {
    isDragging.value = false;
    window.removeEventListener('pointermove', onMove);
    window.removeEventListener('pointerup', onUp, true);
    window.removeEventListener('pointercancel', onUp, true);
    window.removeEventListener('blur', onUp);
    if (moved) canvas.scheduleSave();
  };
  window.addEventListener('pointermove', onMove);
  window.addEventListener('pointerup', onUp, true);
  window.addEventListener('pointercancel', onUp, true);
  window.addEventListener('blur', onUp);
}

function onStartConnect(event: PointerEvent, cardId: string, kind: PortKind) {
  const pos = screenToWorld(event.clientX, event.clientY);
  connecting.value = { cardId, kind, x: pos.x, y: pos.y };

  const onMove = (e: PointerEvent) => {
    if (e.buttons === 0) {
      onUp();
      return;
    }
    const p = screenToWorld(e.clientX, e.clientY);
    if (connecting.value) {
      connecting.value.x = p.x;
      connecting.value.y = p.y;
    }
  };
  const onUp = () => {
    connecting.value = null;
    window.removeEventListener('pointermove', onMove);
    window.removeEventListener('pointerup', onUp);
    window.removeEventListener('pointercancel', onUp);
    window.removeEventListener('blur', onUp);
  };
  window.addEventListener('pointermove', onMove);
  window.addEventListener('pointerup', onUp);
  window.addEventListener('pointercancel', onUp);
  window.addEventListener('blur', onUp);
}

function onFinishConnect(targetId: string, kind: PortKind) {
  if (!connecting.value) return;
  const srcCard = canvas.cards.find((c) => c.id === connecting.value!.cardId);
  canvas.connectNodes(connecting.value.cardId, targetId, kind, srcCard?.name);
  connecting.value = null;
}

function onDoubleClick(event: MouseEvent) {
  if ((event.target as HTMLElement).closest('.rh-node-wrap, .group-box, .quick-menu, .bottom-bar, .composer-host')) {
    return;
  }
  const world = screenToWorld(event.clientX, event.clientY);
  const rect = boardRef.value!.getBoundingClientRect();
  quickMenu.value = {
    screenX: Math.min(rect.width - 190, Math.max(72, event.clientX - rect.left)),
    screenY: Math.min(rect.height - 200, Math.max(72, event.clientY - rect.top)),
    worldX: world.x,
    worldY: world.y,
  };
}

function createFromQuickMenu(type: NodeType, extra?: Partial<CanvasCard>) {
  if (!quickMenu.value) return;
  canvas.addNode(type, quickMenu.value.worldX, quickMenu.value.worldY, extra);
  quickMenu.value = null;
}

function fitToContainer() {
  if (!boardRef.value) return;
  const rect = boardRef.value.getBoundingClientRect();
  canvas.fitView(rect.width, rect.height - 100);
}

function handleAutoLayout() {
  canvas.autoLayout();
  fitToContainer();
}

function addNodeAtCenter(payload: { type: NodeType; extra?: Partial<CanvasCard> }) {
  if (!boardRef.value) {
    canvas.addNode(payload.type, 160, 120, payload.extra);
    return;
  }
  const rect = boardRef.value.getBoundingClientRect();
  const center = screenToWorld(rect.left + rect.width / 2, rect.top + rect.height / 2 - 40);
  const offset = (canvas.cards.length % 5) * 28;
  canvas.addNode(payload.type, center.x - 100 + offset, center.y - 100 + offset, payload.extra);
}

function onKeyDown(e: KeyboardEvent) {
  if (e.code === 'Space' && !(e.target as HTMLElement)?.closest('input,textarea,select,[contenteditable="true"]')) {
    spacePressed.value = true;
  }
  if (e.key === 'Escape') quickMenu.value = null;
}

function onKeyUp(e: KeyboardEvent) {
  if (e.code === 'Space') spacePressed.value = false;
}

onMounted(() => {
  window.addEventListener('keydown', onKeyDown);
  window.addEventListener('keyup', onKeyUp);
});

onBeforeUnmount(() => {
  window.removeEventListener('keydown', onKeyDown);
  window.removeEventListener('keyup', onKeyUp);
});

function onBoardDragOver(event: DragEvent) {
  if (event.dataTransfer?.types?.includes('application/x-rh-history-output')) {
    event.preventDefault();
    event.dataTransfer.dropEffect = 'copy';
  }
}

function onBoardDrop(event: DragEvent) {
  const raw = event.dataTransfer?.getData('application/x-rh-history-output');
  if (!raw) return;
  event.preventDefault();
  try {
    const item = JSON.parse(raw);
    const world = screenToWorld(event.clientX, event.clientY);
    canvas.restoreFromHistoryOutput(item, Math.round(world.x - 110), Math.round(world.y - 110));
  } catch {
    // ignore malformed drop payload
  }
}

defineExpose({ fitToContainer, addNodeAtCenter, handleAutoLayout });
</script>

<template>
  <section
    ref="boardRef"
    class="rhtv-board"
    :class="{ panning: isPanning, 'space-ready': spacePressed }"
    :style="{
      '--gx': canvas.viewport.x + 'px',
      '--gy': canvas.viewport.y + 'px',
      '--gs': 22 * canvas.viewport.z + 'px',
    }"
    @pointerdown="onBoardPointerDown"
    @wheel.prevent="onWheel"
    @dblclick="onDoubleClick"
    @dragover="onBoardDragOver"
    @drop="onBoardDrop"
  >
    <div
      class="world"
      :style="{
        transform: `translate(${canvas.viewport.x}px, ${canvas.viewport.y}px) scale(${canvas.viewport.z})`,
      }"
    >
      <!-- Optional Group Boxes -->
      <section
        v-for="g in groupBoxes"
        :key="g.id"
        class="group-box"
        :style="{ left: g.x + 'px', top: g.y + 'px', width: g.w + 'px', height: g.h + 'px' }"
      >
        <div class="group-header" @pointerdown.stop="onGroupDragStart($event, g.id)">
          <input
            :value="g.name"
            class="group-name"
            @pointerdown.stop
            @change="canvas.renameGroup(g.id, ($event.target as HTMLInputElement).value)"
          />
          <small>{{ g.cards.length }} 节点</small>
          <button
            class="grp-btn"
            type="button"
            @pointerdown.stop
            @click="canvas.runNodesInOrder(g.cards.map((c) => c.id))"
          >
            <Play :size="11" /> 运行
          </button>
          <button
            class="grp-btn"
            type="button"
            title="解组"
            @pointerdown.stop
            @click="canvas.ungroup(g.id)"
          >
            <Ungroup :size="11" />
          </button>
        </div>
      </section>

      <!-- Smooth RHTV Bezier Edges -->
      <svg class="edges-svg">
        <path
          v-for="edge in edges"
          :key="edge.key"
          :d="edge.path"
          class="edge-path"
          :class="{ active: edge.active, dimmed: focus.active && !edge.active }"
          @click.stop="canvas.disconnectEdge(edge.from, edge.to, edge.kind)"
        >
          <title>点击断开连线</title>
        </path>
        <path v-if="draftEdgePath && connecting" :d="draftEdgePath" class="edge-path draft" />
      </svg>

      <!-- Nodes -->
      <CanvasNode
        v-for="card in canvas.cards"
        :key="card.id"
        :card="card"
        :class="nodeFocusClass(card)"
        :connecting-from="connecting ? { cardId: connecting.cardId, kind: connecting.kind } : null"
        @drag-start="onNodeDragStart"
        @start-connect="onStartConnect"
        @finish-connect="onFinishConnect"
      />

      <!-- Highlighted edges drawn above cards so they never look like they end at a card they pass behind -->
      <svg v-if="activeEdges.length" class="edges-svg edges-overlay">
        <path v-for="edge in activeEdges" :key="'ov-' + edge.key" :d="edge.path" class="edge-path overlay" />
      </svg>

      <!-- @图片N badges on upstream cards of the selected video node -->
      <div
        v-for="b in refBadges"
        :key="'badge-' + b.id"
        class="ref-badge"
        :style="{ left: b.x + 'px', top: b.y + 'px' }"
      >
        {{ b.text }}
      </div>
    </div>

    <!-- Marquee Selection -->
    <div
      v-if="marquee"
      class="marquee-box"
      :style="{
        left: Math.min(marquee.x1, marquee.x2) + 'px',
        top: Math.min(marquee.y1, marquee.y2) + 'px',
        width: Math.abs(marquee.x2 - marquee.x1) + 'px',
        height: Math.abs(marquee.y2 - marquee.y1) + 'px',
      }"
    />

    <!-- Double-Click Quick Create Menu -->
    <div
      v-if="quickMenu"
      class="quick-menu"
      :style="{ left: quickMenu.screenX + 'px', top: quickMenu.screenY + 'px' }"
      @pointerdown.stop
    >
      <div class="quick-title">快捷添加节点</div>
      <button
        type="button"
        @click="createFromQuickMenu('video', { name: 'Seedance 2.0 Mini', videoMode: 'omni', width: 210, height: 280 })"
      >
        <i class="lime" /> 视频生成（全能参考）
      </button>
      <button
        type="button"
        @click="createFromQuickMenu('source', { mediaKind: 'image', name: '图片素材', width: 176, height: 220 })"
      >
        <i /> 图片素材
      </button>
      <button
        type="button"
        @click="createFromQuickMenu('source', { mediaKind: 'video', name: '动作参考视频', width: 176, height: 118 })"
      >
        <i /> 视频素材
      </button>
      <button
        type="button"
        @click="createFromQuickMenu('source', { mediaKind: 'audio', name: '背景音乐', width: 176, height: 112 })"
      >
        <i /> 音频素材
      </button>
    </div>

    <!-- Empty Hint -->
    <div v-if="!canvas.cards.length" class="empty-hint">
      <b>双击空白处或点击左侧 + 号添加节点</b>
      <span>将图片 / 视频 / 音频连线到「视频生成」节点，选中即可使用全能参考 @编排</span>
    </div>

    <!-- Bottom-Left RHTV Floating Zoom & View Bar -->
    <div class="bottom-bar" @pointerdown.stop>
      <div class="view-pill">
        <button
          type="button"
          class="view-btn"
          :class="{ active: showMinimap }"
          title="显示/隐藏小地图"
          @click="showMinimap = !showMinimap"
        >
          <MapIcon :size="14" />
        </button>
        <button
          type="button"
          class="view-btn"
          :class="{ active: !showEdges }"
          title="显示/隐藏连线"
          @click="showEdges = !showEdges"
        >
          <Eye v-if="showEdges" :size="14" />
          <EyeOff v-else :size="14" />
        </button>
        <button type="button" class="view-btn" title="重置视图" @click="fitToContainer">
          <RotateCcw :size="14" />
        </button>
        <input
          type="range"
          min="0.25"
          max="1.8"
          step="0.05"
          :value="canvas.viewport.z"
          class="zoom-slider"
          @input="canvas.viewport.z = Number(($event.target as HTMLInputElement).value)"
        />
        <span class="zoom-pct">{{ Math.round(canvas.viewport.z * 100) }}%</span>
      </div>

      <button type="button" class="util-pill" title="整理画布" @click="handleAutoLayout">
        <LayoutGrid :size="14" />
      </button>
    </div>

    <!-- Toggleable Minimap -->
    <aside v-if="showMinimap" class="minimap" @pointerdown.stop>
      <header>
        <b>画布小地图</b>
        <span>{{ canvas.cards.length }} 节点</span>
      </header>
      <div class="minimap-box">
        <div
          v-for="n in minimap.nodes"
          :key="n.id"
          class="minimap-node"
          :class="{ selected: n.selected }"
          :style="{ left: n.x + 'px', top: n.y + 'px', width: n.w + 'px', height: n.h + 'px' }"
        />
      </div>
    </aside>

    <!-- Floating Seedance Omni-Reference Video Composer Panel -->
    <VideoComposerPanel
      v-if="activeVideoCard"
      v-show="!isDragging && !isPanning && !connecting"
      :card="activeVideoCard"
    />
  </section>
</template>

<style scoped>
.rhtv-board {
  position: relative;
  width: 100%;
  height: 100%;
  overflow: hidden;
  cursor: grab;
  background-color: var(--bg);
  background-image: radial-gradient(circle, var(--grid-dot) 1px, transparent 1px);
  background-size: var(--gs, 22px) var(--gs, 22px);
  background-position: var(--gx, 0px) var(--gy, 0px);
}

.rhtv-board.panning {
  cursor: grabbing;
}

.world {
  position: absolute;
  left: 0;
  top: 0;
  transform-origin: 0 0;
  width: 1px;
  height: 1px;
}

.edges-svg {
  position: absolute;
  left: 0;
  top: 0;
  width: 1px;
  height: 1px;
  overflow: visible;
  pointer-events: none;
}

.edge-path {
  fill: none;
  stroke: var(--edge-default);
  stroke-width: 1.6;
  pointer-events: stroke;
  cursor: pointer;
  transition: stroke 0.14s ease, stroke-width 0.14s ease;
}

.edge-path.active {
  stroke: rgba(255, 255, 255, 0.72);
  stroke-width: 1.9;
}

.edge-path:hover {
  stroke: var(--accent);
  stroke-width: 2.8;
}

.edge-path.dimmed {
  opacity: 0.25;
}

.edges-overlay {
  pointer-events: none;
}

.edge-path.overlay {
  stroke: var(--accent);
  stroke-width: 1.8;
  opacity: 0.85;
  pointer-events: none;
}

.focus-dimmed {
  opacity: 0.32;
  filter: grayscale(0.6);
  transition: opacity 0.15s ease, filter 0.15s ease;
}

.focus-related {
  transition: opacity 0.15s ease;
}

.ref-badge {
  position: absolute;
  z-index: 3;
  padding: 2px 6px;
  border-radius: 6px;
  background: var(--accent);
  color: #111;
  font-size: 10.5px;
  font-weight: 700;
  line-height: 1.4;
  white-space: nowrap;
  pointer-events: none;
  box-shadow: 0 2px 6px rgba(0, 0, 0, 0.4);
}

.edge-path.draft {
  stroke: var(--accent);
  stroke-dasharray: 5 4;
  pointer-events: none;
}

.group-box {
  position: absolute;
  border-radius: 16px;
  border: 1px dashed rgba(255, 255, 255, 0.22);
  background: rgba(255, 255, 255, 0.02);
  pointer-events: none;
}

.group-header {
  position: absolute;
  top: 8px;
  left: 12px;
  right: 12px;
  height: 28px;
  display: flex;
  align-items: center;
  gap: 8px;
  pointer-events: auto;
  cursor: grab;
}

.group-name {
  width: 130px;
  border: 0;
  background: transparent;
  color: #ffffff;
  font-weight: 600;
  outline: none;
}

.group-header small {
  flex: 1;
  color: var(--muted);
  font-size: 11px;
}

.grp-btn {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 3px 8px;
  border-radius: 999px;
  border: 1px solid var(--line);
  background: var(--toolbar-surface);
  color: var(--text);
  font-size: 11px;
}

.marquee-box {
  position: absolute;
  z-index: 40;
  border: 1px solid rgba(255, 255, 255, 0.45);
  background: rgba(255, 255, 255, 0.12);
  border-radius: 4px;
  pointer-events: none;
}

.quick-menu {
  position: absolute;
  z-index: 70;
  width: 204px;
  padding: 7px;
  border-radius: 14px;
  background: rgba(25, 25, 27, 0.98);
  border: 1px solid var(--line-strong);
  box-shadow: var(--shadow);
  display: grid;
  gap: 2px;
}

.quick-title {
  padding: 4px 8px;
  font-size: 11px;
  color: var(--muted);
}

.quick-menu button {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 7px 9px;
  border: 0;
  border-radius: 9px;
  background: transparent;
  text-align: left;
  font-size: 12.5px;
}

.quick-menu button:hover {
  background: rgba(255, 255, 255, 0.08);
}

.quick-menu i {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: rgba(255, 255, 255, 0.55);
}

.quick-menu i.lime {
  background: var(--accent);
}

.empty-hint {
  position: absolute;
  inset: 0;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 6px;
  color: var(--muted);
  pointer-events: none;
}

.empty-hint b {
  font-size: 15px;
  color: rgba(255, 255, 255, 0.85);
}

/* Bottom-Left Floating Zoom Bar */
.bottom-bar {
  position: fixed;
  left: 16px;
  bottom: 16px;
  z-index: 45;
  display: flex;
  align-items: center;
  gap: 8px;
}

.view-pill {
  height: 36px;
  padding: 0 10px 0 6px;
  border-radius: 999px;
  background: var(--toolbar-surface);
  border: 1px solid var(--line);
  box-shadow: var(--toolbar-shadow);
  backdrop-filter: blur(20px);
  display: flex;
  align-items: center;
  gap: 4px;
}

.view-btn {
  width: 28px;
  height: 28px;
  border-radius: 50%;
  border: 0;
  background: transparent;
  color: var(--text-secondary);
  display: grid;
  place-items: center;
}

.view-btn:hover,
.view-btn.active {
  color: #ffffff;
  background: rgba(255, 255, 255, 0.1);
}

.zoom-slider {
  width: 66px;
  accent-color: #ffffff;
  margin-left: 4px;
}

.zoom-pct {
  font-size: 11px;
  color: var(--muted);
  min-width: 34px;
  text-align: right;
}

.util-pill {
  width: 36px;
  height: 36px;
  border-radius: 50%;
  border: 1px solid var(--line);
  background: var(--toolbar-surface);
  color: var(--text-secondary);
  box-shadow: var(--toolbar-shadow);
  backdrop-filter: blur(20px);
  display: grid;
  place-items: center;
}

.util-pill:hover {
  color: #ffffff;
  background: var(--surface-selected);
}

.minimap {
  position: fixed;
  left: 16px;
  bottom: 62px;
  width: 186px;
  padding: 8px;
  border-radius: 14px;
  background: rgba(24, 24, 26, 0.94);
  border: 1px solid var(--line-strong);
  box-shadow: var(--shadow);
  z-index: 45;
}

.minimap header {
  display: flex;
  justify-content: space-between;
  font-size: 10.5px;
  color: var(--muted);
  margin-bottom: 6px;
}

.minimap-box {
  position: relative;
  height: 98px;
  border-radius: 8px;
  background: #0d0d0f;
  border: 1px solid var(--line);
  overflow: hidden;
}

.minimap-node {
  position: absolute;
  border-radius: 2px;
  background: rgba(255, 255, 255, 0.35);
}

.minimap-node.selected {
  background: var(--accent);
}
</style>
