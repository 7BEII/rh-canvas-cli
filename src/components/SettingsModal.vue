<script setup lang="ts">
import { onMounted, ref } from 'vue';
import { X, Save, CheckCircle2, Loader2, Sparkles } from 'lucide-vue-next';
import { apiClient } from '@/api/client';
import { useCanvasStore } from '@/stores/canvas';
import { useProjectsStore } from '@/stores/projects';

const emit = defineEmits<{
  (e: 'close'): void;
}>();

const canvas = useCanvasStore();
const projects = useProjectsStore();

const saving = ref(false);
const testing = ref(false);
const testingRh = ref(false);
const testingTensor = ref(false);
const provider = ref<'runninghub' | 'ark' | 'tensor'>('runninghub');
const rhApiKey = ref('');
const arkApiKey = ref('');
const volcAk = ref('');
const volcSk = ref('');
const tensorApiKey = ref('');
const tensorBaseUrl = ref('https://openapi.tensorart.me/canvas-openapi');
const testResult = ref<{ ok: boolean; msg: string } | null>(null);
const rhTestResult = ref<{ ok: boolean; msg: string } | null>(null);
const tensorTestResult = ref<{ ok: boolean; msg: string } | null>(null);

onMounted(async () => {
  try {
    const res = await apiClient.getConfig();
    provider.value = (res.config.video_provider as 'runninghub' | 'ark' | 'tensor') || 'runninghub';
    rhApiKey.value = res.config.runninghub_api_key || '';
    arkApiKey.value = res.config.ark_api_key || '';
    volcAk.value = res.config.volc_ak || '';
    volcSk.value = res.config.volc_sk || '';
    tensorApiKey.value = res.config.tensor_api_key || '';
    tensorBaseUrl.value = res.config.tensor_base_url || 'https://openapi.tensorart.me/canvas-openapi';
  } catch (err: any) {
    canvas.showToast(err?.message || '读取配置失败');
  }
});

async function handleTestArk() {
  testing.value = true;
  testResult.value = null;
  try {
    const res = await apiClient.testArkConnection({ ark_api_key: arkApiKey.value.trim() });
    testResult.value = { ok: true, msg: res.message };
  } catch (err: any) {
    testResult.value = { ok: false, msg: err?.message || '连接测试失败' };
  } finally {
    testing.value = false;
  }
}

async function handleTestRunningHub() {
  testingRh.value = true;
  rhTestResult.value = null;
  try {
    const res = await apiClient.testRunningHubConnection({ runninghub_api_key: rhApiKey.value.trim() });
    rhTestResult.value = { ok: true, msg: res.message };
  } catch (err: any) {
    rhTestResult.value = { ok: false, msg: err?.message || '连接测试失败' };
  } finally {
    testingRh.value = false;
  }
}

async function handleTestTensor() {
  testingTensor.value = true;
  tensorTestResult.value = null;
  try {
    const res = await apiClient.testTensorConnection({
      tensor_api_key: tensorApiKey.value.trim(),
      tensor_base_url: tensorBaseUrl.value.trim(),
    });
    tensorTestResult.value = { ok: true, msg: res.message };
  } catch (err: any) {
    tensorTestResult.value = { ok: false, msg: err?.message || '连接测试失败' };
  } finally {
    testingTensor.value = false;
  }
}

async function handleSave() {
  saving.value = true;
  try {
    await apiClient.saveConfig({
      video_provider: provider.value,
      runninghub_api_key: rhApiKey.value.trim(),
      runninghub_base_url: 'https://www.runninghub.cn',
      ark_api_key: arkApiKey.value.trim(),
      ark_base_url: 'https://ark.cn-beijing.volces.com/api/v3',
      ark_video_model: '',
      ark_image_model: '',
      volc_ak: volcAk.value.trim(),
      volc_sk: volcSk.value.trim(),
      tensor_api_key: tensorApiKey.value.trim(),
      tensor_base_url: tensorBaseUrl.value.trim() || 'https://openapi.tensorart.me/canvas-openapi',
    });
    await projects.checkHealth();
    const msg =
      provider.value === 'runninghub'
        ? '已保存并切换至 RunningHub 渠道'
        : provider.value === 'tensor'
          ? '已保存并切换至 Tensor (Forge) 渠道'
          : '已保存并切换至火山方舟官方渠道';
    canvas.showToast(msg);
    emit('close');
  } catch (err: any) {
    canvas.showToast(err?.message || '保存失败');
  } finally {
    saving.value = false;
  }
}
</script>

<template>
  <div class="backdrop" @click.self="emit('close')">
    <div class="modal">
      <header class="modal-head">
        <div class="head-title">
          <Sparkles :size="16" class="lime" />
          <h3>模型渠道与 API Key</h3>
        </div>
        <button class="btn icon ghost" type="button" @click="emit('close')">
          <X :size="16" />
        </button>
      </header>

      <div class="modal-body">
        <!-- Channel Switcher -->
        <div class="channel-tabs">
          <button
            type="button"
            class="ch-tab"
            :class="{ active: provider === 'runninghub' }"
            @click="provider = 'runninghub'"
          >
            RunningHub 渠道
          </button>
          <button
            type="button"
            class="ch-tab"
            :class="{ active: provider === 'tensor' }"
            @click="provider = 'tensor'"
          >
            Tensor (Forge) API
          </button>
          <button
            type="button"
            class="ch-tab"
            :class="{ active: provider === 'ark' }"
            @click="provider = 'ark'"
          >
            火山方舟官方
          </button>
        </div>

        <!-- RunningHub Panel -->
        <template v-if="provider === 'runninghub'">
          <label class="row">
            <div class="label-row">
              <span>RunningHub API Key（默认开启 realPersonMode，原版真人图直出）</span>
              <button type="button" class="test-btn" :disabled="testingRh" @click="handleTestRunningHub">
                <Loader2 v-if="testingRh" :size="12" class="spin" />
                <CheckCircle2 v-else :size="12" />
                {{ testingRh ? '测试中…' : '测试连通性' }}
              </button>
            </div>
            <input
              v-model="rhApiKey"
              class="field key-input"
              type="password"
              autofocus
              placeholder="粘贴你的 RunningHub API Key…"
              @keydown.enter="handleSave"
            />
          </label>
          <div v-if="rhTestResult" class="test-banner" :class="{ ok: rhTestResult.ok, err: !rhTestResult.ok }">
            {{ rhTestResult.msg }}
          </div>
          <div class="builtin-box">
            <div class="builtin-title">已内置 RunningHub OpenAPI v2 接口</div>
            <div class="model-pills">
              <span class="model-pill">sparkvideo-2.0-mini/multimodal-video</span>
              <span class="model-pill">alibaba/wan-3.0/image-to-video</span>
              <span class="model-pill">alibaba/wan-3.0/text-to-video</span>
              <span class="model-pill">minimax/hailuo-h3/multimodal-to-video</span>
              <span class="model-pill">media/upload/binary</span>
            </div>
          </div>
        </template>

        <!-- Tensor (Forge) Panel -->
        <template v-else-if="provider === 'tensor'">
          <label class="row">
            <div class="label-row">
              <span>Tensor / Forge Access Key（Canvas OpenAPI v1）</span>
              <button type="button" class="test-btn" :disabled="testingTensor" @click="handleTestTensor">
                <Loader2 v-if="testingTensor" :size="12" class="spin" />
                <CheckCircle2 v-else :size="12" />
                {{ testingTensor ? '测试中…' : '测试连通性' }}
              </button>
            </div>
            <input
              v-model="tensorApiKey"
              class="field key-input"
              type="password"
              autofocus
              placeholder="粘贴你的 ak_forge_... Access Key…"
              @keydown.enter="handleSave"
            />
          </label>
          <label class="row">
            <span class="sub-label">接入节点（forge.art 选海外 Tensor，tusi.cn 选国内吐司）</span>
            <select v-model="tensorBaseUrl" class="field">
              <option value="https://openapi.tensorart.me/canvas-openapi">Tensor 海外（openapi.tensorart.me）</option>
              <option value="https://openapi.tusiart.cn/canvas-openapi">吐司国内（openapi.tusiart.cn）</option>
            </select>
          </label>
          <div v-if="tensorTestResult" class="test-banner" :class="{ ok: tensorTestResult.ok, err: !tensorTestResult.ok }">
            {{ tensorTestResult.msg }}
          </div>
          <div class="builtin-box">
            <div class="builtin-title">已内置 Tensor Canvas 生图与视频模型</div>
            <div class="model-pills">
              <span class="model-pill">NANO_BANANA_2 / PRO</span>
              <span class="model-pill">CHATGPT_IMAGE_2</span>
              <span class="model-pill">MIDJOURNEY_V7</span>
              <span class="model-pill">WAN_2_7_IMAGE / VIDEO</span>
              <span class="model-pill">SEEDANCE_2_0(_FAST)</span>
              <span class="model-pill">KLING_V3(_OMNI)</span>
              <span class="model-pill">VIDU_Q3_PRO / PIXVERSE_V6</span>
            </div>
          </div>
        </template>

        <!-- Volcengine Ark Panel -->
        <template v-else>
          <label class="row">
            <div class="label-row">
              <span>火山方舟官方 API Key</span>
              <button type="button" class="test-btn" :disabled="testing" @click="handleTestArk">
                <Loader2 v-if="testing" :size="12" class="spin" />
                <CheckCircle2 v-else :size="12" />
                {{ testing ? '测试中…' : '测试连通性' }}
              </button>
            </div>
            <input
              v-model="arkApiKey"
              class="field key-input"
              type="password"
              placeholder="粘贴你的火山方舟 API Key…"
              @keydown.enter="handleSave"
            />
          </label>

          <div v-if="testResult" class="test-banner" :class="{ ok: testResult.ok, err: !testResult.ok }">
            {{ testResult.msg }}
          </div>

          <div class="aksk-box">
            <div class="builtin-title">自动录入真人图私域资产库（可选 · 填火山 IAM Access Key）</div>
            <div class="split">
              <input v-model="volcAk" class="field" placeholder="Access Key ID (AK)" />
              <input v-model="volcSk" class="field" type="password" placeholder="Secret Access Key (SK)" />
            </div>
          </div>
        </template>
      </div>

      <footer class="modal-foot">
        <button class="btn" type="button" @click="emit('close')">取消</button>
        <button class="btn primary" type="button" :disabled="saving" @click="handleSave">
          <Save :size="15" />
          {{ saving ? '保存中…' : '保存' }}
        </button>
      </footer>
    </div>
  </div>
</template>

<style scoped>
.backdrop {
  position: fixed;
  inset: 0;
  z-index: 120;
  background: rgba(0, 0, 0, 0.72);
  backdrop-filter: blur(6px);
  display: grid;
  place-items: center;
  padding: 20px;
}

.modal {
  width: min(560px, 100%);
  border-radius: 18px;
  background: var(--surface);
  border: 1px solid var(--line-strong);
  box-shadow: var(--shadow);
  overflow: hidden;
}

.modal-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 15px 18px;
  border-bottom: 1px solid var(--line);
}

.head-title {
  display: flex;
  align-items: center;
  gap: 8px;
}

.modal-head h3 {
  margin: 0;
  font-size: 15px;
}

.lime {
  color: var(--accent);
}

.modal-body {
  padding: 18px;
  display: grid;
  gap: 14px;
}

.channel-tabs {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 6px;
  padding: 4px;
  border-radius: 12px;
  background: var(--panel);
  border: 1px solid var(--line);
}

.ch-tab {
  border: 1px solid transparent;
  border-radius: 9px;
  padding: 8px 10px;
  background: transparent;
  color: var(--muted);
  font-size: 12px;
  font-weight: 500;
  transition: background 0.14s ease, color 0.14s ease, border-color 0.14s ease;
}

.ch-tab.active {
  background: var(--surface-selected);
  border-color: var(--accent-border);
  color: var(--accent);
  font-weight: 600;
}

.row {
  display: grid;
  gap: 8px;
}

.sub-label {
  font-size: 12px;
  color: var(--muted);
  line-height: 1.45;
}

.label-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  font-size: 12px;
  color: var(--muted);
}

.key-input {
  min-height: 40px;
  font-size: 13.5px;
}

.test-btn {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  height: 26px;
  padding: 0 10px;
  border-radius: 999px;
  border: 1px solid var(--line-strong);
  background: var(--surface-group);
  color: var(--text);
  font-size: 11.5px;
  white-space: nowrap;
  flex-shrink: 0;
}

.test-btn:hover:not(:disabled) {
  border-color: var(--accent);
  color: var(--accent);
}

.test-banner {
  padding: 9px 11px;
  border-radius: 10px;
  font-size: 12px;
}

.test-banner.ok {
  background: rgba(204, 255, 0, 0.12);
  color: var(--accent);
  border: 1px solid var(--accent-border);
}

.test-banner.err {
  background: rgba(255, 107, 107, 0.14);
  color: #ffc2c2;
  border: 1px solid rgba(255, 107, 107, 0.4);
}

.aksk-box,
.builtin-box {
  padding: 11px 12px;
  border-radius: 12px;
  background: var(--panel);
  border: 1px solid var(--line);
  display: grid;
  gap: 8px;
}

.split {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 8px;
}

.builtin-title {
  font-size: 11.5px;
  color: var(--muted);
}

.model-pills {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.model-pill {
  padding: 2px 8px;
  border-radius: 6px;
  background: var(--surface-group);
  border: 1px solid var(--line);
  font-size: 11px;
  color: rgba(255, 255, 255, 0.78);
  font-family: ui-monospace, Consolas, monospace;
}

.modal-foot {
  padding: 12px 18px;
  border-top: 1px solid var(--line);
  display: flex;
  justify-content: flex-end;
  gap: 8px;
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
