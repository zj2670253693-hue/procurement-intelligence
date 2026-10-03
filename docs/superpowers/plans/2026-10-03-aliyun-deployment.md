# 阿里云轻量环境部署实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将现有招采实体提取平台以可复现、可重启、适配 2 核 4 GB 的方式部署到 Ubuntu 24.04 阿里云服务器，并导入当前 1038 篇公告和 6856 条实体结果。

**Architecture:** 公网流量由 Nginx 接收，静态提供 Vue 构建产物，并将 `/api` 转发到只监听 `127.0.0.1:8000` 的单进程 Uvicorn。后端通过 systemd 运行，MySQL 仅监听本机；应用默认使用 2 个提取线程，OCR 单实例串行运行。首阶段传输代码、前端构建产物和现有结构化结果，不传输 3.5 GB 原始附件。

**Tech Stack:** Ubuntu 24.04、Python 3.12、FastAPI、Uvicorn、Vue 3、Vite、Nginx、MySQL 8、systemd、PowerShell 发布脚本

**Spec:** `docs/superpowers/specs/2026-10-03-aliyun-deployment-design.md`

## Global Constraints

- 目标环境为 Ubuntu 24.04、2 vCPU、约 3.4 GiB 可见内存和约 49 GB 系统盘。
- Uvicorn 固定使用 1 个进程，后端提取线程和附件分析线程默认均为 2。
- RapidOCR 继续惰性加载并串行推理；每个 PDF 默认最多 OCR 5 页。
- MySQL 只监听本机，InnoDB Buffer Pool 为 256 MB，最大连接数为 30。
- 只公开 SSH 和 HTTP；不公开 8000、3306。
- 不把 `.env`、API Key、SSH 私钥、密码、`node_modules`、Windows MySQL 数据目录打进发布包。
- 首次部署只导入 `raw_results.json` 的现有结果，不上传原始附件包，也不声称旧 `evaluation_report.json` 是真实准确率。
- 服务器系统安装由 `admin` 执行；应用进程和文件归 `zhoujin`，不给 `zhoujin` 长期 sudo 权限。
- 所有系统配置在语法检查通过后才能重载；当前 SSH 管理会话在端到端验收前保持打开。

## Review Focus

- 环境变量把并发数设置为 0、负数或非法字符串时，应用应拒绝非法字符串，并将非正数钳制为 1；Task 1 的资源配置测试覆盖。
- 发布包中出现 `.env`、私钥、`node_modules` 或缓存文件时，构建应失败；Task 3 的发布包审计覆盖。
- Bootstrap 重复执行时，不得重复创建 Swap、数据库账号或破坏现有环境文件；Task 4 的二次执行检查覆盖。
- 后端未启动或升级失败时，Nginx 静态前端仍可读取，API 返回明确的 502，上一版本可回滚；Task 6 的故障演练覆盖。
- 数据库重复导入同一份结果时，公告与实体计数不得翻倍；Task 5 的二次导入检查覆盖。

---

### Task 1: 将运行并发调整为 2 核 4 GB 默认值

**Files:**
- Modify: `task1_entity_extraction/config.py:82-105`
- Modify: `task1_entity_extraction/main.py:63-68`
- Modify: `task1_entity_extraction/analyze_attachments.py:115`
- Modify: `task1_entity_extraction/api/routers/task3.py:105`
- Modify: `task1_entity_extraction/test_framework.py`
- Modify: `task1_entity_extraction/.env.example`

**Interfaces:**
- Consumes: 环境变量 `PROCESS_MAX_WORKERS`，未设置时使用 `2`。
- Produces: `config.PROCESS_MAX_WORKERS: int`，CLI、上传流水线和附件分析统一使用该值。

- [ ] **Step 1: 写入失败测试 `test_low_memory_worker_configuration()`**

在 `test_framework.py` 中临时清除 `PROCESS_MAX_WORKERS` 后重载 `config`，断言默认值为 `2`；设置为 `0` 和 `-3` 时断言为 `1`；设置为 `4` 时断言为 `4`；设置为 `abc` 时断言重载配置会抛出 `ValueError`；最后恢复原环境变量并重载模块。

- [ ] **Step 2: 运行测试并确认失败**

Run: `python test_framework.py`

Expected: FAIL，提示 `config` 没有 `PROCESS_MAX_WORKERS`。

- [ ] **Step 3: 在 `config.py` 定义资源配置**

新增 `PROCESS_MAX_WORKERS = max(1, int(os.getenv("PROCESS_MAX_WORKERS", "2")))`。保留 `OCR_MAX_PAGES_PER_PDF=5`、`OCR_RENDER_SCALE=2.0` 和现有 OCR 串行锁。

- [ ] **Step 4: 让所有入口消费统一配置**

将 `main.py` 的 `--workers` 默认值、`analyze_attachments.py` 的线程池和 `api/routers/task3.py` 的流水线构造参数改为 `config.PROCESS_MAX_WORKERS`；在 `.env.example` 添加 `PROCESS_MAX_WORKERS=2`。

- [ ] **Step 5: 运行测试并确认通过**

Run: `python test_framework.py`

Expected: 所有框架测试通过，资源配置测试显示默认并发为 2。

- [ ] **Step 6: 提交资源适配修改**

```bash
git add task1_entity_extraction/config.py task1_entity_extraction/main.py task1_entity_extraction/analyze_attachments.py task1_entity_extraction/api/routers/task3.py task1_entity_extraction/test_framework.py task1_entity_extraction/.env.example
git commit -m "perf: tune extraction concurrency for 2c4g"
```

### Task 2: 增加可审计的生产部署模板

**Files:**
- Create: `deploy/procurement-api.service`
- Create: `deploy/nginx-procurement-app.conf`
- Create: `deploy/mysql-low-memory.cnf`
- Create: `deploy/procurement.env.example`
- Create: `deploy/bootstrap-ubuntu.sh`
- Create: `deploy/finalize-services.sh`
- Modify: `task1_entity_extraction/test_framework.py`

**Interfaces:**
- Consumes: `/srv/procurement-app/current/backend`、`/srv/procurement-app/shared/venv`、`/etc/procurement-app/procurement.env`。
- Produces: `procurement-api.service`、Nginx 站点、MySQL 低内存配置、幂等的管理员安装脚本。

- [ ] **Step 1: 写入失败测试 `test_deployment_assets()`**

断言六个部署文件存在；systemd 文件包含 `User=zhoujin`、`WorkingDirectory=/srv/procurement-app/current/backend`、`--host 127.0.0.1`、`--workers 1`；Nginx 文件包含静态根目录、`location /api/` 和 `proxy_pass http://127.0.0.1:8000`；MySQL 文件包含 `bind-address = 127.0.0.1`、`innodb_buffer_pool_size = 256M`、`max_connections = 30`。

- [ ] **Step 2: 运行测试并确认失败**

Run: `python test_framework.py`

Expected: FAIL，提示部署文件不存在。

- [ ] **Step 3: 创建 systemd、Nginx、MySQL 和环境模板**

`procurement-api.service` 使用 1 个 Uvicorn worker、`Restart=on-failure` 和 `EnvironmentFile=/etc/procurement-app/procurement.env`。Nginx 只代理 `/api/`，静态根目录指向 `/srv/procurement-app/current/frontend-dist`，上传上限设为 `512m`，API 超时设为 `600s`。环境模板包含数据库变量、模型变量、`PROCESS_MAX_WORKERS=2` 和现有 OCR 限制，但不得包含真实密码或 API Key。

- [ ] **Step 4: 创建幂等 `bootstrap-ubuntu.sh`**

脚本必须检查以 root 执行；安装 `python3-venv`、`python3-pip`、`nginx`、`mysql-server`、`unzip`、`curl`；仅在系统无 Swap 时创建 `/swapfile` 2 GB；创建并将 `/srv/procurement-app` 交给 `zhoujin:projectteam`；首次运行时生成数据库随机密码并写入权限为 `0640 root:projectteam` 的环境文件；使用 MySQL socket 管理账号幂等创建数据库和本机应用账号；复制部署模板但不在 `current` 不存在时启动应用。

- [ ] **Step 5: 创建 `finalize-services.sh`**

脚本必须先检查 `current/backend`、`current/frontend-dist` 和共享虚拟环境存在；运行 `nginx -t`；运行 `systemctl daemon-reload`；启用并重启 MySQL、后端和 Nginx；最后以 `curl --fail http://127.0.0.1/api/health` 验证。

- [ ] **Step 6: 运行本地测试并在 Ubuntu 上做脚本语法检查**

Run locally: `python test_framework.py`

Run on Ubuntu staging copy: `bash -n deploy/bootstrap-ubuntu.sh deploy/finalize-services.sh`

Expected: 框架测试通过；`bash -n` 无输出且退出码为 0。

- [ ] **Step 7: 提交部署模板**

```bash
git add deploy task1_entity_extraction/test_framework.py
git commit -m "ops: add lightweight ubuntu deployment assets"
```

### Task 3: 构建前端和无敏感信息发布包

**Files:**
- Create: `deploy/build-release.ps1`
- Modify: `.gitignore`
- Test: `deploy/build-release.ps1` 自检阶段

**Interfaces:**
- Consumes: `frontend/`、`task1_entity_extraction/` 和 Task 2 的 `deploy/`。
- Produces: `deploy/artifacts/procurement-app-<timestamp>.zip`，内部固定包含 `backend/`、`frontend-dist/`、`deploy/` 三个顶层目录。

- [ ] **Step 1: 写发布包自检规则**

脚本在压缩前和压缩后都扫描文件清单，若出现 `.env`、`id_rsa`、`id_ed25519`、`node_modules`、`__pycache__`、`.git`、`mysql-data` 或 `uploads` 则退出 1。要求发布包包含 `backend/output/raw_results.json`、`backend/requirements.txt`、`frontend-dist/index.html` 和全部部署模板。

- [ ] **Step 2: 首次运行并确认因脚本未完成而失败**

Run: `powershell -ExecutionPolicy Bypass -File deploy/build-release.ps1 -AuditOnly`

Expected: FAIL，提示发布包构建功能尚未实现或缺少必需文件。

- [ ] **Step 3: 实现 `build-release.ps1`**

脚本运行 `npm ci` 和 `npm run build`；将必要后端源码与现有结构化输出复制到临时目录；排除 `.env`、缓存、上传临时文件和验证集；复制 `frontend/dist` 为 `frontend-dist`；复制部署目录；生成 ZIP；执行清单审计；输出 ZIP 绝对路径、大小和 SHA-256，不输出任何敏感配置。

- [ ] **Step 4: 构建并审计发布包**

Run: `powershell -ExecutionPolicy Bypass -File deploy/build-release.ps1`

Expected: 前端构建成功；ZIP 生成；清单审计通过；发布包明显小于原始附件包。

- [ ] **Step 5: 解压到临时目录进行烟雾检查**

断言四个必需文件存在；扫描禁用文件名为 0；读取 `raw_results.json` 断言有 1038 个公告和 6856 个实体。

- [ ] **Step 6: 提交发布构建脚本**

```bash
git add deploy/build-release.ps1 .gitignore
git commit -m "build: add audited production release packaging"
```

### Task 4: 初始化 Ubuntu 系统环境

**Files:**
- Deploy: Task 3 生成的发布 ZIP 到 `/home/zhoujin/deployment/`
- Execute: ZIP 内 `deploy/bootstrap-ubuntu.sh`

**Interfaces:**
- Consumes: `admin` 的 sudo 权限和发布包内的部署模板。
- Produces: 系统软件、2 GB Swap、MySQL 数据库和账号、`/srv/procurement-app`、生产环境文件及未启动的服务定义。

- [ ] **Step 1: 记录部署前基线**

Run: `nproc`, `free -h`, `df -h /`, `ss -lnt`, `systemctl is-active nginx mysql`。

Expected: 2 CPU、约 3.4 GiB 内存、足够磁盘；初次部署时 Nginx/MySQL 未运行。

- [ ] **Step 2: 以 `zhoujin` 上传发布包并校验 SHA-256**

Run locally: `scp <release.zip> zhoujin@<SERVER_IP>:/home/zhoujin/deployment/`

Run remotely: `sha256sum /home/zhoujin/deployment/<release.zip>`

Expected: 本地和服务器 SHA-256 一致。

- [ ] **Step 3: 在 `admin` 会话解压部署脚本并执行 Bootstrap**

由用户在现有 `admin` 窗口执行发布包中的 `bootstrap-ubuntu.sh`。脚本不得打印生成的数据库密码。

- [ ] **Step 4: 验证系统初始化结果**

断言 `swapon --show` 有 2 GB Swap；Nginx、MySQL、Python venv 工具已安装；`/srv/procurement-app` 属于 `zhoujin:projectteam`；环境文件权限为 `0640`；3306 只监听本机。

- [ ] **Step 5: 再运行一次 Bootstrap 验证幂等性**

Expected: 不创建第二个 Swap，不覆盖环境文件中的现有密码，MySQL 用户创建不报冲突，目录权限保持正确。

### Task 5: 安装应用、导入数据并验证幂等性

**Files:**
- Install: `/srv/procurement-app/releases/<timestamp>/backend`
- Install: `/srv/procurement-app/releases/<timestamp>/frontend-dist`
- Create/update: `/srv/procurement-app/current` 软链接
- Create/update: `/srv/procurement-app/shared/venv`

**Interfaces:**
- Consumes: Task 4 的系统环境、数据库环境文件和 Task 3 的发布包。
- Produces: 可由 systemd 启动的当前版本和包含 1038 篇公告、6856 条实体的数据库。

- [ ] **Step 1: 以 `zhoujin` 解压为版本目录**

目录名使用发布包时间戳；解压完成后验证 `backend`、`frontend-dist` 和 `deploy` 完整存在。

- [ ] **Step 2: 创建共享虚拟环境并安装依赖**

Run: `python3 -m venv /srv/procurement-app/shared/venv`，随后使用该环境的 `pip install -r backend/requirements.txt`。

Expected: `python -c` 能导入 `fastapi`、`uvicorn`、`pymysql`、`rapidocr`、`pypdfium2` 和项目模块。

- [ ] **Step 3: 在切换 `current` 前运行框架测试**

Run: `/srv/procurement-app/shared/venv/bin/python backend/test_framework.py`

Expected: 全部框架测试通过；不调用外部 LLM。

- [ ] **Step 4: 原子更新 `current` 软链接**

使用临时软链接后重命名，确保任何时刻 `current` 都指向完整版本；记录上一版本路径供回滚。

- [ ] **Step 5: 加载生产环境并导入结构化结果**

从 `/etc/procurement-app/procurement.env` 加载数据库配置，运行 `backend/import_to_db.py`，不传 HTML 目录。

Expected: 输出公告 1038 篇、标的物 6856 条。

- [ ] **Step 6: 重复执行导入验证幂等性**

再次运行相同命令，查询四张表；公告仍为 1038、实体仍为 6856，关系和投标人不得翻倍。

### Task 6: 启动服务并完成端到端验收

**Files:**
- Execute: `/home/zhoujin/deployment/finalize-services.sh` 或发布包对应脚本
- Verify: Nginx、MySQL、`procurement-api.service`

**Interfaces:**
- Consumes: Task 5 的 `current` 版本、共享虚拟环境和已导入数据库。
- Produces: 公网可访问、开机自启并通过健康检查的应用。

- [ ] **Step 1: 由 `admin` 执行服务收尾脚本**

脚本先检查 Nginx 配置，再执行 daemon reload、enable 和 restart。

- [ ] **Step 2: 检查三项服务状态和监听地址**

Expected: Nginx、MySQL、后端均为 `active`；公网监听只有 22 和 80；8000、3306 仅监听 `127.0.0.1`。

- [ ] **Step 3: 检查本机和公网健康接口**

Run remotely: `curl --fail http://127.0.0.1/api/health`

Run locally: `curl http://<SERVER_IP>/api/health`

Expected: HTTP 200，返回公告和实体统计。

- [ ] **Step 4: 验证前端核心路径**

在浏览器打开 `http://<SERVER_IP>/`，确认首页加载；进入实体列表，执行关键词查询和分页，结果来自服务器数据库。

- [ ] **Step 5: 验证重启恢复**

由 `admin` 重启 `procurement-api`、Nginx 和 MySQL，确认服务恢复且数据计数不变。服务器整机重启仅在用户明确同意维护窗口后执行。

- [ ] **Step 6: 做故障与回滚演练**

停止后端时确认静态前端仍能打开且 `/api` 明确返回 502；恢复服务后健康检查重新为 200。将 `current` 临时切回上一版本并验证可启动，再恢复新版本。

- [ ] **Step 7: 记录资源基线**

记录空闲状态和一次典型查询后的 `free -h`、`ps`、`systemctl status`；确认无 OOM、无反复重启、磁盘剩余大于 10 GB。

### Task 7: 安全收尾和部署记录

**Files:**
- Create: `docs/deployment/aliyun-2c4g-runbook.md`
- Update: `docs/任务一-业务流程说明.md`

**Interfaces:**
- Consumes: Task 6 的实际服务路径、端口、版本、资源数据和验证结果。
- Produces: 不含密码/API Key 的比赛部署说明和复现手册。

- [ ] **Step 1: 写部署运行手册**

记录架构、服务管理、日志查看、升级、回滚、数据库备份和资源限制；只记录变量名，不记录秘密值。

- [ ] **Step 2: 更新任务一文档中的部署与实测状态**

明确区分“附件解析已全量重跑”“实体结果仍待最新版 OCR 重跑”“旧 100% 评测无效”。写入实际健康检查、数据行数和资源占用。

- [ ] **Step 3: 运行最终验证**

Run locally: `git diff --check`、Python 框架测试、前端生产构建。

Run remotely: 服务状态、健康接口、表计数、监听端口和资源检查。

Expected: 所有检查通过，或在部署记录中准确列出未通过项，不得把未验证内容写成完成。

- [ ] **Step 4: 提交部署记录**

```bash
git add docs/deployment/aliyun-2c4g-runbook.md docs/任务一-业务流程说明.md
git commit -m "docs: record aliyun 2c4g deployment"
```

- [ ] **Step 5: 完成凭据收尾**

提醒用户立即更换已在聊天中出现过的 SSH 密码；确认普通用户无额外 sudo 权限；确认生产环境文件权限保持 `0640`；不得在自动化中代替用户提交密码修改。
