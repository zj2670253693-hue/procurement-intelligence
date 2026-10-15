# 任务三：隔离平台运行与验收

## 当前状态（2026-10-15）

任务一、二线上版本不变。此分支已实现上传、账号、持久队列、实体/关系数据集隔离、筛选、来源与导出、前端交互；已进行本地合成测试与真实浏览器渲染检查。

**尚不是已验收的云端发布版本。** 还需真实隔离 MySQL、公网 HTTPS、三用户同时查询加后台处理、Ubuntu cgroup 总内存、真实模型小样本。生产API/worker已接入同一服务端配置工厂，但实际付费默认关闭；旧 `PAID_INFERENCE_ENABLED` 不能绕过。当前 `finalize_release()` 仅校验操作员报告结构，返回 `applied=false`，没有可执行的特权切换路径。不得将合成数据报告改名后充当真实验收报告。

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
4. 配置有效公网 HTTPS 与域名/证书、同源 `TASK3_PUBLIC_ORIGIN`。若由 Nginx 终止 TLS，只信任明确配置的 loopback 代理，设置 `TASK3_TRUSTED_PROXIES`；不能盲信任任意 `X-Forwarded-Proto`。现有 HTTP 展示不作为私有登录/付费操作的安全通道。
5. 先运行独立预览实例与单 worker；不要切换 `/srv/procurement-app/current`。预览与正式 worker 共用服务器级 named lock，不能同时处理。
6. 单 worker unit 的 `MemoryMax=1024M` 是**worker 和全部孩子总和**，`MemorySwapMax=0`、`KillMode=control-group`；API drop-in 为 768M。不能仅用 Python 主进程 RSS 冒充总 cgroup 峰值，也不能用 Windows 测试证明 Linux 限额。

## 默认离线验证

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

## 浏览器及性能

`deploy/task3/browser_acceptance.cjs` 使用操作员提供的 Playwright/浏览器，对真实 HTTPS、已发布自有批次做五种查询冷/热两轮，测量从请求前到表格/关系图完成两帧渲染。账号仅在环境/浏览器内存中，输出不含密码/cookie。设置 `TASK3_ACCEPT_ORIGIN`、`TASK3_ACCEPT_DATASET`、`TASK3_ACCEPT_USERNAME`、`TASK3_ACCEPT_PASSWORD` 后执行。不能将本地 mock API 的 315.92ms 均值当成阿里云结论。

此脚本不是完整并发/安全验收：仍须三独立账号同时使用，加后台真实处理，核对越权、数据集切换、刷新续传、取消/重试、部分失败、原文脚本不执行、附件与三表下载；记录每场景均值小于5秒、API/worker cgroup peak、system available minimum、swap、OOM事件、冷/热与并发时间段。任何缺项不得放行。

## 模型与费用（当前关闭）

真实抽取必须另行取得小样本授权，明确 owner/provider/model、最多3篇、最多8次请求及有限输入/输出 token 预算。64000/32768 只是计划示例，不是授权。生产接线已有，但必须另行提供并核实与准确模型版本一致的 token 上界 counter/profile；不能把任意字节估计标记 verified，不能只打开旧环境开关就调用。

服务端需同时设置 `TASK3_PAID_ENABLED=1` 和 `TASK3_INFERENCE_PROFILE=<私有绝对路径>`。策略结构见 `deploy/task3/inference-profile.example.json`（示例approved=false，不可直接启用）。策略包含明确owner、provider/model/HTTPS base URL、有限总预算、生成参数、counter代码及验证记录哈希。密钥仅来自服务器对应环境变量，不放策略/请求/浏览器。

策略及其同目录 `counters/` 内插件必须无软链接、不可group/world写；部署建议root所有、worker只读。API只核验有限大小的策略/代码哈希和已登记记录，不实例化SDK/tokenizer；worker首次处理时才加载已登记代码的 `build_counter()`，要求 `verified is True`、`profile_sha256`完全匹配且 `supports(provider,model)` 为真，再建立max_retries=0的账本transport。counter需实现 `upper_bound(InferenceRequest)->正整数`，其正确性由实际模型/模板/tokenizer版本验证记录证明，**不是approved布尔值或源码哈希本身证明**。验证记录、权重/tokenizer资产、实际策略和密钥不进Git。

API准入和worker每次发送前重新核对开关/策略身份；撤销、文件漂移、预算不足、counter验证失败均停止，不自动降级到旧客户端。计数器插件属于受信任的管理员代码，不接受网页上传替换；没有已核实插件时保留关闭。发布后更改策略/模型/生成或解析配置会改变processing指纹；worker需重启载入新配置，不能复用旧任务冒充新结果。API密钥轮换本身不是算法身份，但应安全重启worker以切换持有的客户端。

SDK 自动重试为0；每次请求先持久 reserve/sent，usage 不明保持 null/uncertain 并阻止自动重发。离线关系构建 model_calls=0 与上游抽取费用分列。

## 发布及回滚前不可省略

所有实际验收与全分支独立审查修复完成后，才制作管理员切换脚本：冻结新包清单和各文件哈希；对旧任务一五表及任务二事实/证据/版本做完整前后指纹；保留旧 env、旧 release、旧 runtime、旧数据库；原子替换新环境和 current 链接。新环境保留旧 DB_NAME/TASK2_DATASET_ID，只新增 TASK3_* 配置，付费默认关闭。

失败须恢复原 env/current、停止新 worker、重启旧 API，再核对旧健康与指纹。不得删除失败新库/旧数据以“回滚”。当前未提供已验证的任务三管理员自动切换程序，旧 task1-task2/admin_cutover.py 固定旧目标，**不能直接复用运行**。
