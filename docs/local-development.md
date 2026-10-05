# 本地开发

## Conda 环境与统一配置

项目使用 Conda 环境 `paper2formula`，Python 3.11 和 Node.js 22，Python 包版本由 `pyproject.toml` 约束，前端由 npm 安装。无需创建 Python 虚拟环境。所有应用配置放在仓库根目录 `.env`；后端读取此文件，Vite 也从根目录读取它。

`environment.yml` 安装 `requirements.lock.txt` 中实际验证过的 Python 依赖快照，再安装本项目；Windows 专用的 `pywin32` 使用平台条件。前端的 `package-lock.json` 固定完整 npm 依赖，可用 `npm ci --prefix apps/web` 重现。

已有 `.env` 时安装脚本不会覆盖。首次手动安装：

```powershell
Copy-Item .env.example .env
conda env create -f environment.yml
conda run -n paper2formula npm install --prefix apps/web
```

这些命令需在仓库根目录执行，使 `-e .[dev]` 安装本项目。已建环境可运行 `conda env update -n paper2formula -f environment.yml`。默认脚本会自动创建或更新环境，Conda 只在自定义路径安装时可传 `-CondaExe`。若仓库已存在 `.conda/env`，脚本会优先使用该本地 Conda 环境；手动命令相应使用 `conda run -p .conda/env ...` 或 `conda activate ./.conda/env`。

也可在 `.env` 设置 `CONDA_ENV_PREFIX=./.conda/env` 显式选择本地 Conda 环境。PowerShell 执行策略禁止脚本时，用 `powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\setup.ps1` 启动，开发与停止脚本同理，避免修改系统的持久执行策略。

配置支持 `KEY=value` 与整行 `#` 注释。PowerShell 启动脚本读取 `.env` 为纯文本，不执行其中内容；不要使用 shell 表达式或变量展开，带空格的路径可使用双引号。更改后重新启动 API；更改 `VITE_` 变量后重新启动前端。

## 启动与日志

使用安装与后台启动脚本：

```powershell
.\scripts\setup.ps1
.\scripts\dev.ps1
```

日志和进程记录位于 `data/runtime/`，`api.err.log`、`web.err.log` 显示启动错误；启动后需要几秒钟服务才会就绪。浏览器打开 `http://localhost:5173`。停止当前脚本记录的会话：

```powershell
.\scripts\stop.ps1
```

若需要直接观察日志，也可以在两个终端分别运行：

```powershell
conda activate paper2formula
python -m apps.api
```

```powershell
conda activate paper2formula
npm --prefix apps/web run dev
```

`API_HOST`、`API_PORT`、`WEB_PORT` 在 `.env` 中修改。开发服务器通过 `/api/v1` 代理访问 API，`VITE_API_BASE_URL=/api/v1` 可保持不变；API 和 Web 分别部署时需要调整 API 地址与 `CORS_ORIGINS`。

## 本地数据与身份

默认 `DATABASE_URL=sqlite:///./data/paper2formula.db`，文件保存到 `STORAGE_DIR=./data`，本地调度使用 `QUEUE_BACKEND=local`。数据库和文件在服务重启后保留；不要把 `data/` 中的论文或日志提交到 Git。

默认 `AUTH_MODE=development`，API 要求开发令牌，前端从 `VITE_DEV_AUTH_TOKEN` 读取同一令牌。`DEV_AUTH_TOKEN` 与前端令牌必须一致。此模式将身份固定为 `DEV_OWNER_ID`，用于单用户本地开发；上线前需接入正式身份系统，不能把此默认令牌当作生产认证。

API 另提供 `AUTH_MODE=jwt` 的 Bearer JWT 校验；配置至少 32 字符的 `JWT_SECRET` 以及 `JWT_ISSUER`、`JWT_AUDIENCE`。JWT 的签发、登录和前端会话接入需要由部署方提供，不能仅替换开发令牌就视为完成正式身份系统。

结果版本在任务内编号。阅读器链接携带 `task_id`、`version` 与 `anchor`，固定到具体任务的具体结果；历史草稿仅供已授权用户只读查看。复核请求同时校验当前任务 revision，结果和复核分页的 revision 不一致时前端重新读取，避免提交旧页面上的内容。

## 规则基线与模型

`MODEL_PROVIDER=heuristic` 不访问模型服务，用提取到的文本生成公式候选、变量候选及明确的未知项，实际运行时处理上传的文件，不依赖人工示例。其适用范围是常见文本公式；图片公式、复杂排版和专业语义需要后续模型与评测。

编号公式的分子、分母或编号可能被 PDF 拆成不同文本行。检测会按编号附近的紧凑数学文本和页面位置组合候选，保留各行的原始证据；模型收到完整公式截图。几何组合本身不保证分式与上下标的 LaTeX 转写正确，规则模式仍需人工核对。纯图片公式、无编号公式与跨页组合尚不支持。

启用模型时修改 `.env`：

```dotenv
MODEL_PROVIDER=openai_compatible
MODEL_API_KEY=your-key
MODEL_BASE_URL=https://api.openai.com/v1
MODEL_NAME=your-model
```

此处 `openai_compatible` 表示 OpenAI 兼容协议，可将 `MODEL_BASE_URL` 指向支持该协议的服务。地址需填写该服务的 API 基础地址（包含服务要求的 `/v1` 等路径）；代码会追加 `/chat/completions`。地址、密钥和模型名称必须匹配，模型需支持图片输入与 JSON 输出。只填写密钥和模型名称、保持 `MODEL_PROVIDER=heuristic` 时，仍使用本地规则。密钥仅配置在后端，不能添加 `VITE_` 前缀。模型只接收所需的证据上下文；输出仍经过证据校验与人工复核。`MODEL_TIMEOUT`、`MODEL_MAX_CALLS`、`MODEL_MAX_TOKENS` 与 `TASK_TIMEOUT` 控制调用及任务预算。

阿里云百炼北京地域可配置 `MODEL_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1`，模型名称使用账号已开通且支持视觉与 JSON 输出的模型，例如 `qwen3.8-max-0902`。密钥必须来自同一地域；其他地域或业务空间专属域名参见[阿里云接口地址说明](https://www.alibabacloud.com/help/en/model-studio/base-url)。修改配置后重启服务，并创建新任务以重新解析已有文档。

## DOCX 与规范 PDF

PDF 是所有证据坐标和批注的基准。DOCX 原件保留，只使用转换后的 PDF 页码。安装 LibreOffice 后，将 `.env` 中 `LIBREOFFICE_PATH` 设置为 `soffice` 或完整路径，例如：

```dotenv
LIBREOFFICE_PATH="C:/Program Files/LibreOffice/program/soffice.exe"
CONVERSION_TIMEOUT=90
```

默认 API 使用限时的本地转换子进程。`infra/converter/` 提供单独的禁网络容器，同时限制内存、CPU、进程数，使用只读根文件系统和非 root 用户。该容器是可选的手动隔离转换路线，未自动替代默认 API 转换。

使用时把 DOCX 放在 `data/conversion/`，按下述方式转换，再把得到的 PDF 上传：

```powershell
New-Item -ItemType Directory -Force data/conversion
# 将需要转换的文件复制为 data/conversion/input.docx。
docker compose --env-file .env -f infra/compose.yml --profile converter run --build --rm converter input.docx
```

输出为 `data/conversion/output/input.pdf`。Linux 主机的挂载目录需允许 UID 10001 写入输出，Windows Docker Desktop 通常通过共享目录权限管理。构建镜像需要联网安装 LibreOffice，运行转换时网络被禁用。

## 可选 PostgreSQL、Redis 与 MinIO

默认启动不依赖 Docker。如需接近原架构的数据与队列组件：

```powershell
docker compose --env-file .env -f infra/compose.yml up -d postgres redis minio
```

修改根目录 `.env`：

```dotenv
DATABASE_URL=postgresql+psycopg://paper2formula:local-paper2formula-database@127.0.0.1:5432/paper2formula
CHECKPOINT_DATABASE_URL=postgresql://paper2formula:local-paper2formula-database@127.0.0.1:5432/paper2formula
QUEUE_BACKEND=celery
REDIS_URL=redis://127.0.0.1:6379/0
CELERY_BROKER_URL=redis://127.0.0.1:6379/0
STORAGE_BACKEND=s3
S3_ENDPOINT_URL=http://127.0.0.1:9000
S3_ACCESS_KEY=paper2formula
S3_SECRET_KEY=local-paper2formula-storage
S3_BUCKET=paper2formula
```

MinIO 控制台地址为 `http://127.0.0.1:9001`，使用 `.env` 的 S3 凭据登录。创建 `paper2formula` bucket 后启动应用与独立 Worker：

```powershell
.\scripts\dev.ps1 -WithWorker
```

或者手动在 Conda 环境中运行 `python -m workers.run`。Compose 端口只绑定本机，卷保存数据；`docker compose --env-file .env -f infra/compose.yml down` 停止组件并保留卷。

PostgreSQL/Redis 镜像限制在指定主版本，MinIO 使用显式 release 标签；目标生产环境仍应固定已验证的镜像 digest，完成备份、监控和隔离测试。首版节点缓存仍包含本地工作路径，API 与 Worker 宜在同一主机和工作目录使用；跨主机重放的路径可移植性尚需完善。上述为接入路线，本地 SQLite 测试通过不等同于分布式部署验收。

## MCP 工具入口

独立 MCP Server 使用本地 stdio，继承同一 `.env` 和可信开发身份：

```powershell
conda activate paper2formula
python -m packages.paper_tools.server
```

将此命令配置到 MCP 客户端，并把工作目录设为仓库根目录。stdio 模式没有网页；工具输入采用服务器已有的文档/任务 ID，不接受任意本地文件路径。默认 `TOOL_TRANSPORT=local` 使用进程内领域工具。设置 `TOOL_TRANSPORT=mcp` 后，工作流通过同主机 stdio 子进程调用 MCP 工具，由服务端传递可信身份和任务范围。受保护 HTTP MCP 部署尚未实现。

## 验证与设计边界

```powershell
conda run -n paper2formula python -m pytest
conda run -n paper2formula python scripts/check_docs.py
conda run -n paper2formula npm --prefix apps/web run build
```

Web/API 接口以运行中的 `/docs` 为准。[架构设计](architecture.md)、[工作流设计](workflow.md)、[接口草案](interfaces.md) 和 [路线图](roadmap.md) 保留原始方案。Docling、高精度公式识别、完整量纲推理、正式身份认证、生产 HTTP MCP、遥测和评测指标均需继续落实。源文件、复核决定与最终导出的版本一致性是首版检查重点。

启动脚本基于 [Conda run 官方说明](https://docs.conda.io/projects/conda/en/stable/commands/run.html) 使用 `--no-capture-output`，容器隔离配置遵循 [Docker Compose 服务参数](https://docs.docker.com/reference/compose-file/services/)。
