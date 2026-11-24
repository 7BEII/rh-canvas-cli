# RH Canvas Studio (`rh-canvas-cli`)

完全脱离 `RH_PDtools_pro` 的独立无限画布工作台，采用 **Vue 3 + TypeScript + Vite + Pinia** 自研无限画布核心，搭配完全独立的 **Python / Flask** 后端服务。

## 目录结构

- `src/`：Vue 3 前端（全部画布首页、预设模板、自研无限画布、节点卡片、编组、小地图、任务日志、设置弹窗）
- `server/`：独立 Python 后端（项目管理、素材上传/静态服务、本地设置、节点运行引擎、生产模式托管 `dist/`）
- `data/`：本项目专属持久化目录（`projects/`、`assets/`、`outputs/`、`settings.json`）

## 启动方式

### 方式一：一键生产启动（推荐）
直接双击 `start.bat`，或执行：
```bash
pnpm install
pnpm build
python -m server.app
```
浏览器打开 `http://127.0.0.1:5188` 即可使用。

### 方式二：前后端热更新开发模式
1. 启动 Python 后端（默认端口 `5188`）：
   ```bash
   python -m server.app
   ```
2. 启动 Vue 3 开发服务器（默认端口 `5178`，已自动代理 `/api` 到 `5188`）：
   ```bash
   pnpm dev
   ```
3. 浏览器打开 `http://127.0.0.1:5178`。

## JSON 兼容性

本项目 `data/projects/canvas-*.json` 与原 RH 无限画布快照格式兼容，可直接通过首页「导入 JSON」导入既有画布项目，也可随时导出为 JSON 备份。

## 核心能力与近期更新

- **多渠道生图 / 生视频**：支持 **Tensor / 吐司 Canvas OpenAPI**（海外 `openapi.tensorart.me` / 国内 `openapi.tusiart.cn`）、**RunningHub**、**火山方舟（Ark）**；Tensor 渠道会自动将参考图/视频/音频通过 `/v1/workflow/actions` 挂载为云端画布持久节点并绑定 `workflowContext` 引用。
- **输入素材时长自动兜底（2–15s）**：上传视频/音频入库（`server/asset_store.py`）以及提交 Tensor 生成前（`server/tensor_client.py`）都会自动检测时长——超过 `14.8s` 自动裁切至 `14.8s`，短于 `2.0s` 自动补齐至 `2.1s`，避免触发 Seedance `must be 2–15 seconds long` 报错。
- **报错原始信息一键 Copy**：画布节点报错浮层（`src/components/CanvasNode.vue`）和右侧「运行日志」抽屉（`src/components/TaskDrawer.vue`）均提供 `Copy` 按钮，支持一键复制完整原始报错文本便于排查。
