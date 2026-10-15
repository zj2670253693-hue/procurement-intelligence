# 任务三：隔离平台运行与验收

## 当前状态（2026-10-15）

任务一、二线上版本不变。此分支已实现上传、账号、持久队列、实体/关系数据集隔离、筛选、来源与导出、前端交互；已进行本地合成测试与真实浏览器渲染检查。

**尚不是已验收的云端发布版本。** 真实隔离 MySQL 边界探针已通过（同名隔离、精确小数、领取/租约/回滚/owner，旧九表指纹不变）；这不是完整平台闭环验收。还需公网 HTTPS、三用户同时查询加后台处理、Ubuntu cgroup 总内存、真实模型小样本。生产API/worker已接入同一服务端配置工厂，但实际付费默认关闭；旧 `PAID_INFERENCE_ENABLED` 不能绕过。当前 `finalize_release()` 仅校验操作员报告结构，返回 `applied=false`，没有可执行的特权切换路径。不得将合成数据报告改名后充当真实验收报告。

## 数据与权限边界

- `DB_NAME`、`TASK2_DATASET_ID` 保留现有官方冻结库/事实版本，官方适配器只读。
- `TASK3_DB_NAME` 必须指向另一个专用库；真实验收库仅接受 `procurement_task3_accept_` 前缀，不复用任务一的数据库授权。
- 平台 reviewer/admin 是独立账号，不是 Linux 的 admin/zhoujin。密码由 `deploy/task3/provision_user.py` 隐藏交互输入，不发到聊天、日志或 Git。
- 匿名任务入口已禁用（包括旧共享guest配置），不能用共享owner代替独立会话；当前所有上传、进度、取消及产物访问均需个人平台会话。匿名隔离会话后续单独实现再开放。
- `TASK3_RUNTIME_ROOT` 为绝对路径，例如 `/srv/procurement-app/shared/runtime/task3-accept`，必须在发布目录外且没有软链接/目录联接。
- 私有输入、原始结果、请求/用量账本、验收报告、截图、release_assets 和实际环境文件均不得提交 GitHub。

## 管理员前置（不自动执行）

1. 确认旧服务健康、当前 release/env/官方库指纹并备份。当前线上任务一计数基线为 1038/6784/14110；计数只是健康检查，不能代替内容哈希。
2. 在阿里云 Ubuntu24.04 的 2vCPU/4GB 主机新建专用任务三验收库，授予现有应用账号**仅该库**权限；不要修改旧库或授予全局权限。
3. 管理员显式调用 `task3_platform.migrate.migrate_platform(connection, expected_database)`，连接 `create_db=False`，一次安装13个t3表和4个独立t2依赖表。API/worker 启动和任务关系导入没有 DDL。再用 provision_user 创建三名平台用户。
4. 配置有效公网 HTTPS 与域名或 IP 证书、同源 `TASK3_PUBLIC_ORIGIN`。若由 Nginx 终止 TLS，只信任明确配置的 loopback 代理，设置 `TASK3_TRUSTED_PROXIES`；不能盲信任任意 `X-Forwarded-Proto`。现有 HTTP 展示不作为私有登录/付费操作的安全通道。
5. 先运行独立预览实例与单 worker；不要切换 `/srv/procurement-app/current`。预览与正式 worker 共用服务器级 named lock，不能同时处理。
6. 单 worker unit 的 `MemoryMax=1024M` 是**worker 和全部孩子总和**，`MemorySwapMax=0`、`KillMode=control-group`；API drop-in 为 768M。不能仅用 Python 主进程 RSS 冒充总 cgroup 峰值，也不能用 Windows 测试证明 Linux 限额。

## 无域名的 IP HTTPS 准备

`deploy/task3/configure_ip_tls.py` 由管理员显式执行，需同时提供 `--apply --accept-ca-agreement-and-public-ip --ip-address <公网IPv4> --expected-host <hostname输出> --expected-nginx-sha256 <旧站点文件SHA256>`。操作者必须先同意 [Let's Encrypt 订户协议](https://letsencrypt.org/repository/) 和证书公开记录 IP。不在仓库保存实际主机名、部署 IP、证书或账户私钥。

脚本限定 root、Ubuntu24.04、预期主机和旧 Nginx 文件哈希；不读取应用环境、不碰数据库、不切换 current、不启用任务三。保留 HTTP 原有路径，用独立 webroot 完成验证；在独立 `/opt/procurement-certbot58` 安装 Certbot5.8.0（不改应用 venv），先 staging dry-run 再签正式证书。独立 `/etc/letsencrypt-procurement` 保存证书/账户；其顶层按 Certbot `--strict-permissions` 要求为 root所有0755，内部 accounts/archive/live 由 Certbot 创建为0700，私钥不出服务器。不能递归 chmod755。签发后检查 CA 信任链、IP SAN、剩余有效期及旧健康计数，再做一次带 reload hook 的续期 dry-run。

只有以上步骤成功才启用 `procurement-ip-renew.timer`，每6小时检查、最多随机延迟30分钟，开机补执行；Certbot 仅在需要时续期，成功后 `nginx -t && systemctl reload nginx`。约6天的证书依赖该定时器；后续应检查 `systemctl list-timers procurement-ip-renew.timer` 与 `journalctl -u procurement-ip-renew.service`，不能以“定时器 active”冒充未来续期已成功。无邮箱注册不提供邮件告警，故运行告警仍需另配。

失败会尝试停用新定时器、移出本次新 unit、恢复原站点文件并 reload；备份及失败文件保留在 `/var/lib/procurement-ip-tls/attempt-*`。即使停定时器失败也继续恢复站点，并明确报告恢复不完整，不覆盖已有同名 unit。再次执行前应由管理员检查上次产物，不盲目重复签证书。

成功报告仅为 `host_local_ip_tls_preparation`：还要从公网、不跳过证书验证地访问 HTTPS。若本机成功而公网失败，检查阿里云入站 TCP443 和主机防火墙；不要开放 MySQL3306。HTTP 暂保留兼容现有展示，私有登录必须使用 HTTPS。此脚本本身不表示任务三已上线。[IP 证书和 webroot 官方说明](https://letsencrypt.org/2026/03/11/shorter-certs-certbot)，[Certbot 续期和 deploy hook](https://eff-certbot.readthedocs.io/en/stable/using.html#renewing-certificates)。

## 默认离线验证

准备代码包可用 `deploy/task3/build_preview_bundle.py --frontend-dist <已校验构建目录> --output <不存在的新ZIP>`。只读取 Git 已跟踪且允许的 Python 包/requirements 与已构建前端，包含 Task3、Task2、标准导出工具及平铺后的旧核心；排除私有结果、原始数据、`.env`、未跟踪文件和测试目录。源码未提交变更会拒绝以 HEAD 标记，已有包不会覆盖。每个文件有SHA清单，ZIP本身也有SHA。冻结 `release_assets` 不进这个代码包，后续应在服务器内从当前冻结版本复制并核对。`code_bundle_prepared` 不是已安装/已验收报告；它也不能证明操作者提供的 dist 与源代码语义一致，仍需保留独立的前端构建/浏览器证据。

`deploy/task3/prepare_preview.py --bundle <服务器代码ZIP> --bundle-sha256 <已核对SHA> --destination /srv/procurement-app/releases/task3-preview-<编号> --expected-current-release <当前解析后的真实release路径> --old-manifest-sha256 <旧manifest的SHA>` 只准备新目录。它核对代码包精确清单、旧manifest及全部冻结展示文件，复制时再次核对哈希，并拒绝已有目标、私有环境文件、路径逃逸、链接和旧资产清单外文件；预留4GiB磁盘。旧 `.env` 和旧后端代码不会顺带复制，只有明确列出的冻结 `release_assets` 在服务器内部复制。失败的部分新目录保留供排查，不自动删除。`files_prepared` 不迁移DB、不创建账号、不改环境/服务/Nginx/current，也不代表实际功能已验收。

```text
python -B -m pytest -q
node --test frontend/tests/*.test.js
npm run build                 # 在 frontend 目录、锁定依赖安装完成后
```

默认测试不连接真实模型/生产库。SQLite/Fake 的通过数与真实 SQL 的通过数分开报告。

## 显式真实 MySQL 检查

只在专用验收库、worker 停止且没有其他 queued/running 工作时设置以下变量。密码通过安全会话临时提供，不写入命令历史示例或仓库。

```text
TASK3_MYSQL_TEST_DB=procurement_task3_accept_<专用测试编号>
TASK3_MYSQL_TEST_WRITE_AUTHORIZED=1
TASK3_MYSQL_TEST_USER=<应用账号>
TASK3_MYSQL_TEST_PASSWORD=<仅本机会话持有>
python -B -m pytest task3_tests/test_mysql_integration.py -m mysql_integration -q
```

测试只连接本机 127.0.0.1；保留本次合成记录便于排查，不删表、不清库。检查实际 DECIMAL(65,30)、事务失败回滚、同公告不同批次、owner 拒绝、竞争领取、租约恢复和旧 epoch 拦截。未配置时 skip，**skip 不代表真实 SQL 通过**。

也可由管理员/已获新库授权的操作员运行 `deploy/task3/run_mysql_acceptance.py --database <新验收库> --allow-test-writes --test-dependencies <独立测试依赖目录> --output <不存在的新报告目录>`。它从服务器环境文件读取凭据但不回显；只读流式哈希旧任务一五表及任务二四表，然后以清理过、模型密钥移除且禁费的环境运行真实SQL测试，再核对旧表指纹。输出 `real_mysql_boundary_probe`，不是完整发布验收；skip、测试失败或旧库变化均不得算通过。报告包含哈希/计数，不含旧原文行。

## 浏览器及性能

`deploy/task3/browser_acceptance.cjs` 使用操作员提供的 Playwright/浏览器，对真实 HTTPS、已发布自有批次做五种查询冷/热两轮，测量从请求前到表格/关系图完成两帧渲染。账号仅在环境/浏览器内存中，输出不含密码/cookie。设置 `TASK3_ACCEPT_ORIGIN`、`TASK3_ACCEPT_DATASET`、`TASK3_ACCEPT_USERNAME`、`TASK3_ACCEPT_PASSWORD` 后执行。不能将本地 mock API 的 315.92ms 均值当成阿里云结论。

此脚本不是完整并发/安全验收：仍须三独立账号同时使用，加后台真实处理，核对越权、数据集切换、刷新续传、取消/重试、部分失败、原文脚本不执行、附件与三表下载；记录每场景均值小于5秒、API/worker cgroup peak、system available minimum、swap、OOM事件、冷/热与并发时间段。任何缺项不得放行。

## 模型与费用（当前关闭）

真实抽取必须另行取得小样本授权，明确 owner/provider/model、最多3篇、最多8次请求及有限输入/输出 token 预算。64000/32768 只是计划示例，不是授权。生产接线已有，但必须另行提供并核实与准确模型版本一致的 token 上界 counter/profile；不能把任意字节估计标记 verified，不能只打开旧环境开关就调用。

服务端需同时设置 `TASK3_PAID_ENABLED=1` 和 `TASK3_INFERENCE_PROFILE=<私有绝对路径>`。策略结构见 `deploy/task3/inference-profile.example.json`（示例approved=false，不可直接启用）。策略包含明确owner、provider/model/HTTPS base URL、有限总预算、生成参数、counter代码及验证记录哈希。密钥仅来自服务器对应环境变量，不放策略/请求/浏览器。

策略及其同目录 `counters/` 内插件必须无软链接、不可group/world写；部署建议root所有、worker只读。API只核验有限大小的策略/代码哈希和已登记记录，不实例化SDK/tokenizer；worker首次处理时才加载已登记代码的 `build_counter()`，要求 `verified is True`、`profile_sha256`完全匹配且 `supports(provider,model)` 为真，再建立max_retries=0的账本transport。counter需实现 `upper_bound(InferenceRequest)->正整数`，其正确性由实际模型/模板/tokenizer版本验证记录证明，**不是approved布尔值或源码哈希本身证明**。验证记录、权重/tokenizer资产、实际策略和密钥不进Git。

API准入和worker每次发送前重新核对开关/策略身份；撤销、文件漂移、预算不足、counter验证失败均停止，不自动降级到旧客户端。计数器插件属于受信任的管理员代码，不接受网页上传替换；没有已核实插件时保留关闭。发布后更改策略/模型/生成或解析配置会改变processing指纹；worker需重启载入新配置，不能复用旧任务冒充新结果。API密钥轮换本身不是算法身份，但应安全重启worker以切换持有的客户端。

SDK 自动重试为0；每次请求先持久 reserve/sent，usage 不明保持 null/uncertain 并阻止自动重发。离线关系构建 model_calls=0 与上游抽取费用分列。

## 隔离预览安装（显式管理员操作）

`deploy/task3/install_preview.py` 只在全部前置校验通过后才写入：非 root、主机名不符、未传 `--apply`、目标库不是 `procurement_task3_accept_` 前缀、`TASK3_RUNTIME_ROOT` 位于 release 内、公开地址不是 `https://`、release 清单 SHA 不符、Nginx 结构不符合“一个80 + 一个443”都会在任何写入前失败。

它创建独立的预览 API（`127.0.0.1:18080`，`MemoryMax=768M`）和预览 worker（`MemoryMax=1024M`），使用独立环境文件，**不改** `/srv/procurement-app/current`、旧 `procurement.env`、旧 worker，也不做数据库 DDL；`DB_NAME`/`TASK2_DATASET_ID` 原样保留，只新增 `TASK3_*` 并把 `TASK3_PAID_ENABLED`、`PAID_INFERENCE_ENABLED` 置 0。环境文件里的模型密钥会被剔除且不打印。

Nginx 只改 HTTPS(443) server 块：`root` 指向预览 `frontend-dist`，`/api/` 与 `/docs` 指向 `127.0.0.1:18080`；HTTP(80) 展示块保持逐字节不变。任何 `/api/` 路径内出现两个上游指令即拒绝执行。写入前会备份站点文件为 `<site>.task3-preview-backup`。

```text
sudo /srv/procurement-app/shared/venv/bin/python -B deploy/task3/install_preview.py \
  --apply --expected-host <hostname> \
  --release /srv/procurement-app/releases/task3-preview-<编号> \
  --release-manifest-sha256 <该目录 manifest 的 SHA256> \
  --source-environment-file /etc/procurement-app/procurement.env \
  --environment-file /etc/procurement-app/procurement-task3-preview.env \
  --database procurement_task3_accept_<编号> --official-database <现有 DB_NAME> \
  --runtime-root /srv/procurement-app/shared/runtime/task3-preview \
  --public-origin https://<同一地址> --nginx-site <当前 443 站点文件> --port 18080
```

安装完成后仍需：真实迁移与三个平台账号、三用户并发加后台处理、cgroup 峰值、五分钟内五场景冷/热渲染、另行授权的有限模型样本。报告中的 `task3_deployed` 只表示预览服务已安装，**不代表**完整验收或正式切换。

## 发布及回滚前不可省略


所有实际验收与全分支独立审查修复完成后，才制作管理员切换脚本：冻结新包清单和各文件哈希；对旧任务一五表及任务二事实/证据/版本做完整前后指纹；保留旧 env、旧 release、旧 runtime、旧数据库；原子替换新环境和 current 链接。新环境保留旧 DB_NAME/TASK2_DATASET_ID，只新增 TASK3_* 配置，付费默认关闭。

失败须恢复原 env/current、停止新 worker、重启旧 API，再核对旧健康与指纹。不得删除失败新库/旧数据以“回滚”。当前未提供已验证的任务三管理员自动切换程序，旧 task1-task2/admin_cutover.py 固定旧目标，**不能直接复用运行**。
