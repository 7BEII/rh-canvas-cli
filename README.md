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
