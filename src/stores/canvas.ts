import { defineStore } from 'pinia';
import { ref, computed } from 'vue';
import { apiClient } from '@/api/client';
import { NODE_DEFS } from '@/data/nodeDefs';
import { useProjectsStore } from '@/stores/projects';
import type {
  CanvasCard,
  CanvasSnapshot,
  CardRef,
  NodeType,
  PortKind,
  TaskLogEntry,
  ViewportState,
} from '@/types/canvas';

interface HistoryEntry {
  cards: CanvasCard[];
}

function deepClone<T>(val: T): T {
  return JSON.parse(JSON.stringify(val));
}

export const useCanvasStore = defineStore('canvas', () => {
  const cards = ref<CanvasCard[]>([]);
  const viewport = ref<ViewportState>({ x: 0, y: 0, z: 1 });
  const seq = ref(1);

  const undoStack = ref<HistoryEntry[]>([]);
  const redoStack = ref<HistoryEntry[]>([]);
  const clipboard = ref<CanvasCard[]>([]);

  const running = ref(false);
  const tasks = ref<TaskLogEntry[]>([]);
  const toastMessage = ref('');
  const activeControllers = new Map<string, AbortController>();
  const runStartedAt = ref<Record<string, number>>({});
  let toastTimer: ReturnType<typeof setTimeout> | null = null;
  let autosaveTimer: ReturnType<typeof setTimeout> | null = null;

  const selectedCards = computed(() => cards.value.filter((c) => c.selected));
  const canUndo = computed(() => undoStack.value.length > 0);
  const canRedo = computed(() => redoStack.value.length > 0);

  const groups = computed(() => {
    const map = new Map<string, { id: string; name: string; cards: CanvasCard[] }>();
    for (const card of cards.value) {
      if (!card.groupId) continue;
      if (!map.has(card.groupId)) {
        map.set(card.groupId, { id: card.groupId, name: card.groupName || '未命名组', cards: [] });
      }
      map.get(card.groupId)!.cards.push(card);
    }
    return Array.from(map.values());
  });

  function showToast(msg: string) {
    toastMessage.value = msg;
    if (toastTimer) clearTimeout(toastTimer);
    toastTimer = setTimeout(() => {
      toastMessage.value = '';
    }, 2600);
  }

  function pushHistory() {
    undoStack.value.push({ cards: deepClone(cards.value) });
    if (undoStack.value.length > 50) undoStack.value.shift();
    redoStack.value = [];
  }

  function scheduleSave() {
    if (autosaveTimer) clearTimeout(autosaveTimer);
    autosaveTimer = setTimeout(() => {
      void persistNow(true);
    }, 900);
  }

  function buildSnapshot(): CanvasSnapshot {
    return {
      format: 'rh-canvas',
      version: 1,
      exportedAt: new Date().toISOString(),
      schemaVersion: 1,
      canvas: {
        cards: deepClone(cards.value),
        seq: seq.value,
        viewport: { ...viewport.value },
      },
    };
  }

  async function persistNow(quiet = false) {
    const projectsStore = useProjectsStore();
    if (!projectsStore.activeProjectId) return;
    try {
      await projectsStore.saveActive(buildSnapshot());
      if (!quiet) showToast('画布已保存');
    } catch (err: any) {
      showToast(`保存失败：${err?.message || '未知错误'}`);
    }
  }

  function loadSnapshot(snapshot?: CanvasSnapshot) {
    const c = snapshot?.canvas;
    cards.value = Array.isArray(c?.cards)
      ? deepClone(c.cards).map((card: CanvasCard) =>
          card.status === 'running' ? { ...card, status: 'idle' } : card,
        )
      : [];
    viewport.value = c?.viewport ? { ...c.viewport } : { x: 80, y: 60, z: 1 };
    seq.value = typeof c?.seq === 'number' ? c.seq : cards.value.length + 1;
    undoStack.value = [];
    redoStack.value = [];
  }

  function uid(prefix = 'n') {
    seq.value += 1;
    return `${prefix}-${seq.value}-${Math.random().toString(36).slice(2, 6)}`;
  }

  function addNode(type: NodeType, x: number, y: number, extra?: Partial<CanvasCard>): CanvasCard {
    pushHistory();
    const def = NODE_DEFS[type];
    const count = cards.value.filter((c) => c.type === type).length + 1;
    const card: CanvasCard = {
      id: uid('n'),
      type,
      name: extra?.name || `${def.title} ${count}`,
      x: Math.round(x),
      y: Math.round(y),
      width: extra?.width || def.defaultWidth || (type === 'source' ? 180 : 240),
      height: extra?.height || (type === 'video' ? 260 : type === 'source' ? 160 : undefined),
      selected: true,
      prompt: '',
      model: def.defaultModel,
      aspect: type === 'image' || type === 'storyboard' || type === 'video' ? '16:9' : undefined,
      resolution: type === 'image' ? '2K' : type === 'video' ? '720p' : undefined,
      shotCount: type === 'storyboard' ? 4 : undefined,
      duration: type === 'video' ? 15 : undefined,
      videoMode: type === 'video' ? 'omni' : undefined,
      generateAudio: type === 'video' ? true : undefined,
      quality: type === 'video' ? 'standard' : undefined,
      batchCount: type === 'video' ? 1 : undefined,
      localMedia: type === 'video' ? [] : undefined,
      refs: [],
      results: [],
      status: 'idle',
      ...extra,
    };
    cards.value.forEach((c) => {
      c.selected = false;
    });
    cards.value.push(card);
    scheduleSave();
    return card;
  }

  function removeCards(ids: string[]) {
    if (!ids.length) return;
    pushHistory();
    const idSet = new Set(ids);
    cards.value = cards.value.filter((c) => !idSet.has(c.id));
    for (const card of cards.value) {
      card.refs = (card.refs || []).filter((r) => !idSet.has(r.from));
    }
    scheduleSave();
  }

  function removeSelected() {
    const ids = selectedCards.value.map((c) => c.id);
    if (!ids.length) return;
    removeCards(ids);
    showToast(`已删除 ${ids.length} 个节点`);
  }

  function selectOnly(id: string | null) {
    for (const c of cards.value) {
      c.selected = id !== null && c.id === id;
    }
  }

  function toggleSelect(id: string) {
    const card = cards.value.find((c) => c.id === id);
    if (card) card.selected = !card.selected;
  }

  function copySelected() {
    if (!selectedCards.value.length) return;
    clipboard.value = deepClone(selectedCards.value);
    showToast(`已复制 ${clipboard.value.length} 个节点`);
  }

  function pasteClipboard() {
    if (!clipboard.value.length) return;
    pushHistory();
    const idMap = new Map<string, string>();
    const clones = deepClone(clipboard.value);
    for (const c of clones) {
      const nextId = uid('n');
      idMap.set(c.id, nextId);
      c.id = nextId;
      c.x += 48;
      c.y += 48;
      c.selected = true;
      delete c.groupId;
      delete c.groupName;
    }
    for (const c of clones) {
      c.refs = (c.refs || [])
        .filter((r) => idMap.has(r.from))
        .map((r) => ({ ...r, from: idMap.get(r.from)! }));
    }
    cards.value.forEach((c) => {
      c.selected = false;
    });
    cards.value.push(...clones);
    scheduleSave();
    showToast(`已粘贴 ${clones.length} 个节点`);
  }

  function undo() {
    const prev = undoStack.value.pop();
    if (!prev) return;
    redoStack.value.push({ cards: deepClone(cards.value) });
    cards.value = prev.cards;
    scheduleSave();
    showToast('已撤销');
  }

  function redo() {
    const next = redoStack.value.pop();
    if (!next) return;
    undoStack.value.push({ cards: deepClone(cards.value) });
    cards.value = next.cards;
    scheduleSave();
    showToast('已恢复');
  }

  function wouldCreateCycle(fromId: string, toId: string): boolean {
    if (fromId === toId) return true;
    const visited = new Set<string>();
    const stack = [fromId];
    while (stack.length) {
      const cur = stack.pop()!;
      if (cur === toId) return true;
      if (visited.has(cur)) continue;
      visited.add(cur);
      const node = cards.value.find((c) => c.id === cur);
      if (!node) continue;
      for (const ref of node.refs || []) {
        stack.push(ref.from);
      }
    }
    return false;
  }

  function connectNodes(fromId: string, toId: string, kind: PortKind, label?: string) {
    if (fromId === toId) return;
    const target = cards.value.find((c) => c.id === toId);
    if (!target) return;
    if (wouldCreateCycle(fromId, toId)) {
      showToast('不能创建循环连线');
      return;
    }
    const exists = (target.refs || []).some((r) => r.from === fromId && r.kind === kind);
    if (exists) {
      showToast('该连线已存在');
      return;
    }
    pushHistory();
    target.refs = [...(target.refs || []), { kind, from: fromId, label }];
    markDownstreamStale(target.id, '上游连线已更新');
    scheduleSave();
  }

  function disconnectEdge(fromId: string, toId: string, kind: PortKind) {
    const target = cards.value.find((c) => c.id === toId);
    if (!target) return;
    pushHistory();
    target.refs = (target.refs || []).filter((r) => !(r.from === fromId && r.kind === kind));
    scheduleSave();
  }

  function markDownstreamStale(sourceId: string, reason = '上游内容已修改') {
    const queue = [sourceId];
    const visited = new Set<string>([sourceId]);
    while (queue.length) {
      const cur = queue.shift()!;
      for (const card of cards.value) {
        if (visited.has(card.id)) continue;
        if ((card.refs || []).some((r) => r.from === cur)) {
          visited.add(card.id);
          if (card.results && card.results.length > 0) {
            card.needsUpdate = true;
            card.staleReason = reason;
          }
          queue.push(card.id);
        }
      }
    }
  }

  function groupSelected() {
    const list = selectedCards.value;
    if (!list.length) {
      showToast('请先选中至少一个节点（可按住 Ctrl 框选）');
      return;
    }
    pushHistory();
    const groupId = uid('grp');
    const name = `组 ${groups.value.length + 1}`;
    for (const c of list) {
      c.groupId = groupId;
      c.groupName = name;
    }
    scheduleSave();
    showToast(`已编组为「${name}」`);
  }

  function ungroup(groupId: string) {
    pushHistory();
    for (const c of cards.value) {
      if (c.groupId === groupId) {
        delete c.groupId;
        delete c.groupName;
      }
    }
    scheduleSave();
    showToast('已取消编组');
  }

  function renameGroup(groupId: string, name: string) {
    for (const c of cards.value) {
      if (c.groupId === groupId) c.groupName = name.trim() || '未命名组';
    }
    scheduleSave();
  }

  function getCardBox(card: CanvasCard) {
    const def = NODE_DEFS[card.type] || NODE_DEFS.prompt;
    const w =
      card.width ||
      (card.type === 'video' ? 210 : card.type === 'source' ? 176 : def.defaultWidth || 260);
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

  function autoLayout() {
    if (!cards.value.length) return;
    pushHistory();

    // Topological depth per node
    const depth = new Map<string, number>();
    const getDepth = (id: string, seen = new Set<string>()): number => {
      if (depth.has(id)) return depth.get(id)!;
      if (seen.has(id)) return 0;
      seen.add(id);
      const card = cards.value.find((c) => c.id === id);
      if (!card || !card.refs?.length) {
        depth.set(id, 0);
        return 0;
      }
      const d = 1 + Math.max(...card.refs.map((r) => getDepth(r.from, new Set(seen))));
      depth.set(id, d);
      return d;
    };
    for (const c of cards.value) getDepth(c.id);

    const layerMap = new Map<number, CanvasCard[]>();
    for (const c of cards.value) {
      const col = depth.get(c.id) || 0;
      if (!layerMap.has(col)) layerMap.set(col, []);
      layerMap.get(col)!.push(c);
    }

    const sortedLayers = Array.from(layerMap.entries())
      .sort((a, b) => a[0] - b[0])
      .map(([, list]) => list);

    const COL_GAP = 16;
    const ROW_GAP = 38; // 紧凑预留节点顶部标题栏高度
    const LAYER_GAP = 150;

    // Pack each topological layer into a tight grid (up to 4 columns) using actual card dimensions
    const layouts = sortedLayers.map((list) => {
      const n = list.length;
      const subCols = n <= 3 ? 1 : n <= 6 ? 2 : n <= 9 ? 3 : 4;
      const rows = Math.ceil(n / subCols);
      const colWidths = new Array<number>(subCols).fill(0);
      const rowHeights = new Array<number>(rows).fill(0);

      list.forEach((card, idx) => {
        const r = Math.floor(idx / subCols);
        const c = idx % subCols;
        const box = getCardBox(card);
        colWidths[c] = Math.max(colWidths[c], box.w);
        rowHeights[r] = Math.max(rowHeights[r], box.h);
      });

      const layerW = colWidths.reduce((a, b) => a + b, 0) + Math.max(0, subCols - 1) * COL_GAP;
      const layerH = rowHeights.reduce((a, b) => a + b, 0) + Math.max(0, rows - 1) * ROW_GAP;
      return { list, subCols, colWidths, rowHeights, layerW, layerH };
    });

    const maxLayerH = Math.max(...layouts.map((l) => l.layerH), 280);
    let cursorX = 60;

    for (const layer of layouts) {
      const startY = 60 + Math.round((maxLayerH - layer.layerH) / 2);

      const colOffsets: number[] = [];
      let cx = 0;
      for (const w of layer.colWidths) {
        colOffsets.push(cx);
        cx += w + COL_GAP;
      }

      const rowOffsets: number[] = [];
      let cy = 0;
      for (const h of layer.rowHeights) {
        rowOffsets.push(cy);
        cy += h + ROW_GAP;
      }

      layer.list.forEach((card, idx) => {
        const r = Math.floor(idx / layer.subCols);
        const c = idx % layer.subCols;
        card.x = Math.round(cursorX + colOffsets[c]);
        card.y = Math.round(startY + rowOffsets[r]);
      });

      cursorX += layer.layerW + LAYER_GAP;
    }

    scheduleSave();
    showToast('已按紧凑网格自动排版');
  }

  function fitView(containerWidth: number, containerHeight: number) {
    if (!cards.value.length) {
      viewport.value = { x: 80, y: 60, z: 1 };
      return;
    }
    const minX = Math.min(...cards.value.map((c) => c.x));
    const minY = Math.min(...cards.value.map((c) => c.y - 28));
    const maxX = Math.max(...cards.value.map((c) => c.x + getCardBox(c).w));
    const maxY = Math.max(...cards.value.map((c) => c.y + getCardBox(c).h));
    const contentW = Math.max(300, maxX - minX + 140);
    const contentH = Math.max(240, maxY - minY + 140);
    const z = Math.max(0.25, Math.min(1.1, Math.min(containerWidth / contentW, containerHeight / contentH)));
    viewport.value = {
      x: Math.round((containerWidth - (maxX - minX) * z) / 2 - minX * z),
      y: Math.round((containerHeight - (maxY - minY) * z) / 2 - minY * z),
      z: Number(z.toFixed(2)),
    };
  }

  async function cancelNode(cardId: string) {
    const card = cards.value.find((c) => c.id === cardId);
    const ctrl = activeControllers.get(cardId);
    if (ctrl) {
      ctrl.abort();
      activeControllers.delete(cardId);
    }
    delete runStartedAt.value[cardId];
    if (card && card.status === 'running') {
      card.status = 'idle';
      card.error = '';
    }
    try {
      const res = await apiClient.cancelNode(cardId);
      showToast(res.message || '已中止生成任务');
    } catch {
      showToast('已中止本地等待');
    }
  }

  async function runSingleNode(cardId: string) {
    const card = cards.value.find((c) => c.id === cardId);
    if (!card) return;
    card.status = 'running';
    card.error = '';
    runStartedAt.value[cardId] = Date.now();
    const upstream = (card.refs || [])
      .map((ref: CardRef) => {
        const up = cards.value.find((c) => c.id === ref.from);
        return up ? { ref, card: up } : null;
      })
      .filter((x): x is { ref: CardRef; card: CanvasCard } => Boolean(x));

    const ctrl = new AbortController();
    activeControllers.set(cardId, ctrl);

    try {
      const res = await apiClient.runNode({ card, upstream }, ctrl.signal);
      card.results = [...(card.results || []), ...res.results];
      card.activeResultIndex = card.results.length - 1;
      card.status = 'done';
      card.needsUpdate = false;
      card.staleReason = '';
      markDownstreamStale(card.id, `上游「${card.name}」已生成新结果`);
      if (res.task) tasks.value.unshift(res.task);
      scheduleSave();
      showToast(`${card.name}：${res.message}`);
    } catch (err: any) {
      if (err?.name === 'AbortError') {
        card.status = 'idle';
        card.error = '';
        return;
      }
      card.status = 'error';
      card.error = err?.message || '运行失败';
      showToast(`${card.name} 失败：${card.error}`);
      throw err;
    } finally {
      activeControllers.delete(cardId);
      delete runStartedAt.value[cardId];
    }
  }

  async function runNodesInOrder(targetIds?: string[]) {
    if (running.value) return;
    const pool = targetIds?.length
      ? cards.value.filter((c) => targetIds.includes(c.id))
      : cards.value;
    if (!pool.length) {
      showToast('画布中没有可运行的节点');
      return;
    }

    // Topological order inside pool
    const poolSet = new Set(pool.map((c) => c.id));
    const ordered: CanvasCard[] = [];
    const visited = new Set<string>();
    const visit = (card: CanvasCard) => {
      if (visited.has(card.id)) return;
      visited.add(card.id);
      for (const ref of card.refs || []) {
        if (poolSet.has(ref.from)) {
          const parent = pool.find((p) => p.id === ref.from);
          if (parent) visit(parent);
        }
      }
      ordered.push(card);
    };
    for (const c of pool) visit(c);

    running.value = true;
    try {
      const runnable = ordered.filter((card) => card.type !== 'source');
      if (!runnable.length) {
        showToast('画布中没有可生成的节点（请添加视频或生图节点）');
        return;
      }
      for (const card of runnable) {
        await runSingleNode(card.id);
      }
      showToast(`运行完成（共 ${runnable.length} 个生成节点）`);
    } catch {
      // Error already surfaced on node & toast
    } finally {
      running.value = false;
    }
  }

  async function refreshTasks() {
    try {
      const res = await apiClient.listTasks(60);
      tasks.value = res.tasks;
    } catch {
      // ignore
    }
  }

  function restoreFromHistoryOutput(
    item: {
      name: string;
      kind: 'video' | 'image';
      url: string;
      meta?: {
        node_type?: string;
        name?: string;
        model?: string;
        prompt?: string;
        aspect?: string;
        resolution?: string;
        duration?: number;
        videoMode?: string;
        stylePreset?: string;
        generateAudio?: boolean;
        provider?: string;
      } | null;
    },
    x: number,
    y: number,
  ) {
    const meta = item.meta;
    if (meta && (meta.node_type === 'image' || meta.node_type === 'video' || meta.prompt || meta.model)) {
      const targetType = meta.node_type === 'video' || item.kind === 'video' ? 'video' : 'image';
      const extra: Partial<CanvasCard> = {
        name: meta.name || (targetType === 'video' ? '回溯视频节点' : '回溯生图节点'),
        model: meta.model || undefined,
        prompt: meta.prompt || '',
        aspect: meta.aspect || undefined,
        resolution: meta.resolution || undefined,
        duration: typeof meta.duration === 'number' ? meta.duration : undefined,
        videoMode: (meta.videoMode as any) || undefined,
        stylePreset: meta.stylePreset || undefined,
        generateAudio: meta.generateAudio,
        status: 'done',
        results: [
          {
            kind: item.kind,
            label: meta.model ? `${meta.model} · 历史回溯` : item.name,
            name: item.name,
            url: item.url,
          },
        ],
        activeResultIndex: 0,
      };
      const created = addNode(targetType, x, y, extra);
      showToast(`已回溯当时生成配置到画布：${created.name}`);
      return created;
    }

    const created = addNode('source', x, y, {
      name: item.name.replace(/\.[^.]+$/, ''),
      mediaKind: item.kind,
      status: 'done',
      results: [
        {
          kind: item.kind,
          label: item.name,
          name: item.name,
          url: item.url,
        },
      ],
      activeResultIndex: 0,
    });
    showToast(`已拖入历史${item.kind === 'video' ? '视频' : '图片'}到画布（早期文件无保存配置）`);
    return created;
  }

  return {
    cards,
    viewport,
    seq,
    selectedCards,
    groups,
    canUndo,
    canRedo,
    running,
    tasks,
    toastMessage,
    showToast,
    pushHistory,
    scheduleSave,
    buildSnapshot,
    persistNow,
    loadSnapshot,
    addNode,
    restoreFromHistoryOutput,
    removeCards,
    removeSelected,
    selectOnly,
    toggleSelect,
    copySelected,
    pasteClipboard,
    undo,
    redo,
    connectNodes,
    disconnectEdge,
    markDownstreamStale,
    groupSelected,
    ungroup,
    renameGroup,
    autoLayout,
    fitView,
    runStartedAt,
    runSingleNode,
    cancelNode,
    runNodesInOrder,
    refreshTasks,
  };
});
