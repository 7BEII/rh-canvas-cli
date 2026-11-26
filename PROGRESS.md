# RH Canvas Studio (`rh-canvas-cli`) 项目进展总结

> 更新时间：2026-11-26  
> 仓库目录：`rh-canvas-cli`（Vue 3 + TypeScript + Vite + Pinia 前端 / Python Flask 独立后端）

---

## 一、项目定位

完全独立运行的无限画布 AI 视频/图像工作台，用于将复杂的多节点创意工作流（参考图/动作视频/配乐 + AI 生图 + 多模态生视频 + 剪辑归档）沉淀为可复用模板，并逐步演进为**「上线版本 = 开放 AppID」**的无头（Headless）AI 应用服务。

---

## 二、已完成的核心能力

### 1. 无限画布工作台与项目管理
- **自研无限画布核心**：支持节点拖拽、连线（图片/视频/音频/文本四类端口）、编组、缩放平移、小地图、历史记录与快照保存。
- **双分区管理（上线版本 / 测试版本）**：
  - 上线版本目录：`data/projects/online/`（同步 GitHub）
  - 测试版本目录：`data/projects/test/`（本地调试，Git 忽略）
  - 支持单选/多选批量上架、下架、删除、导出 ZIP（含关联素材与配置）以及全量工作区迁移包导入导出。
- **2:3 竖版画布卡片与自定义封面**：支持右键或快捷按钮更换/重置预览图、双击内联重命名。

### 2. 多渠道 AI 生成引擎
- **支持三大云端渠道**：
  - **Tensor / 吐司 Canvas OpenAPI**（海外 `openapi.tensorart.me` / 国内 `openapi.tusiart.cn`）：支持参考图/视频/音频持久化挂载与 `workflowContext` 引用。
  - **RunningHub OpenAPI v2**：支持 Seedance 2.5 / 2.5 Flash / 2.0 / 2.0 Fast / 2.0 Mini、阿里 Wan 3.0、海螺 Hailuo H3，以及取消任务后的后台兜底下载防丢片。
  - **火山方舟（Ark）**：支持 Seedream 系列生图与 Seedance 系列生视频。
- **输入素材时长自动兜底（2–15s）**：上传与提交生成前自动检测音视频时长，超长裁切至 `14.8s`，过短补齐至 `2.1s`，避免触发 Seedance 时长限制报错。

### 3. 可观测性与排查体验
- **运行日志与历史产物增强**：记录并展示可读的调用耗时（`duration_ms`）与发起时间（`started_at`），历史产物保存元数据快照（`.meta.json`）。
- **原始报错一键 Copy**：画布节点报错浮层与右侧「运行日志」抽屉均支持一键复制完整原始报错文本。

### 4. 已沉淀画布资产（当前工作区）
- **上线版本（8 个）**：
  - `LibTV 复刻 · Group 2 精简版`（`canvas-46d7dafc...`）
  - `LibTV 复刻 · Group 3 精简版`（`canvas-86adcc72...`）
  - `LibTV 复刻 · Group 4 精简版`（`canvas-945f2b8a...`）
  - `LibTV 复刻 · Group 5 精简版`（`canvas-6548a19f...`）
  - `LibTV 复刻 · Group 6 精简版`（`canvas-a8293ceb...`）
  - `LibTV 复刻 · Group 7 精简版`（`canvas-876b1e3d...`）
  - `LibTV 复刻 · Group 8 精简版`（`canvas-cc6789ea...`）
  - `LibTV 复刻 · Group 9 精简版`（`canvas-98ff6b39...`）
- **测试版本（3 个）**：
  - `LibTV 复刻 · 全能参考精简版`、`LibTV 复刻 · 全能参考精简版 (3)`、`未命名画布`

---

## 三、当前进行中：上线版本开放 AppID（`loadimage` → `save_video`）

目标：将「上线版本」从单纯的文件夹分区升级为类似 RunningHub WebApp 的可调用 AI 应用——画布内固定素材与中间链路全部封装，对外仅开放 `app_id`、输入槽位（如 `loadimage`）与输出槽位（如 `save_video`），采用**异步提交返回 `job_id` + 轮询查结果**的调用模式。

| 模块 | 状态 | 说明 |
| :--- | :---: | :--- |
| **1. `ProjectStore` AppID 与映射数据结构** | ✅ 已完成 | 已在 `server/project_store.py` 实现 `app_config`（含自定义 `app_id` 唯一性校验、`inputs`/`outputs` 槽位规范化）、上架自动推断默认 `loadimage` / `save_video` 映射、`update_app_config`、`get_by_app_id`、`list_online_apps`。 |
| **2. 后端 DAG 整图异步执行引擎 (`server/app_runner.py`)** | ⏳ 待实现 | 内存克隆上线快照 → 注入外部传入的 `loadimage`（URL/文件）→ 按 `refs` 拓扑排序依次复用 `WorkflowRunner.run_node` 执行 → 异步队列与 `job_id` 进度追踪、取消与结果持久化。 |
| **3. 开放 OpenAPI 路由 (`server/app.py`)** | ⏳ 待实现 | `GET /api/apps`、`GET /api/apps/<app_id>`、`PUT /api/projects/<id>/app-config`、`POST /api/apps/<app_id>/run`（返回 `job_id`）、`GET /api/apps/jobs/<job_id>`、`POST /api/apps/jobs/<job_id>/cancel`。 |
| **4. 前端上线应用配置与试跑面板 (`OnlineAppModal.vue` / `ProjectHome.vue`)** | ⏳ 待实现 | 上线卡片展示 `AppID` 一键复制；弹窗支持自定义 `app_id`、勾选绑定 `loadimage` / `save_video` 节点、在线上传图片一键试跑并查看进度与成片、复制 cURL / Python 调用代码。 |
| **5. 联调与验证** | ⏳ 待实现 | 前端 TypeScript/Vite 构建检查，后端 `app_id` 提交 → `job_id` 轮询 → `save_video` 输出全流程验证。 |

---

## 四、下一步建议

继续完成上述 **第 2 ~ 5 步**，即可让现有的 8 个 `LibTV 复刻 · Group 2~9` 上线画布全部直接拥有各自的 `AppID` 和 `loadimage -> save_video` 异步开放接口，外部脚本或矩阵系统即可直接对接调用。
