# 任务三隔离数据集平台实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans 按Native方式逐项实现。Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 新上传数据独立完成解析、抽取、入库、标准导出和五类关系查询，具备持久任务、资源/费用控制及可验证的前端全流程。

**Architecture:** 现有MySQL的`t3_`命名空间与单实例worker；API只接收和查询，任务二构建使用独立离线子进程。保留基准任务一二接口和默认版本；上传数据始终通过作用域路由访问。四批顺序交付同一闭环，不拆成互不兼容的服务。

**Tech Stack:** 已有Python/FastAPI/PyMySQL、Vue/Vite、标准库子进程/哈希/ZIP/XML；不加Redis、图库或前端测试依赖。

**Spec:** ../specs/2026-10-14-task3-isolated-platform-design.md

## Global Constraints

- Ubuntu24.04、2vCPU/4GB；API一个worker、同时处理任务1个、实体/OCR并发1、模型同时在途1。三用户查询与一个处理任务下无OOM，系统可用内存最低512MiB；这是验收目标，非已达标声明。
- 默认仅解析、付费开关关闭；本地测试不联网调用模型，不重跑1038、不改金标/冻结结果/默认`t2_active`。真实小样本需另有明确资源、数据发送及有限费用授权。
- 单请求64MiB、分片16MiB、单文件4GiB、接收合计8GiB、HTML16MiB、公告2000、外层成员10000、附件包成员2000、单附件成员128MiB、解压合计8GiB、压缩比200、任务磁盘12GiB、全局16GiB、预留4GiB、排队10。接收+解压仍受12GiB合计限制；部署前按实际样本元数据和剩余资源复验可配置值。
- 任务墙钟6小时、单公告解析180秒、模型请求120秒；心跳10秒、租约60秒。超时/失联不是请求已撤销的证明。
- `task_id`、`upload_dataset_id`、`task2_dataset_id`分别持久保存。文件与结果按owner/数据集隔离；金额Decimal、未知NULL，数量保留单位，不预算替换/包减法/歧义乘法/按顺序映射。
- 保留旧基准查询；新接口在`/api/task3`，上传范围在`datasets/{upload_dataset_id}`。分页最多200、偏移10000；官方只读条目为`official`。
- 输入/抽取目录与`runtime/task2/artifacts`、`runtime/task2/run_receipts`互不嵌套，无软链接/junction。代码不写原公共output，不公开原文、产物、仲裁、账号或密钥。
- 账号/付费操作需HTTPS或受控安全通道；API不自动DDL，管理员迁移/服务发布是独立操作，不扩大Linux账号权限。

## Review Focus

- 响应已持久化但JSON无效：调用已发生且可重放，不应默默重发；超时才是不同的uncertain路径（Task5、Task6）。
- 租约过期时旧模型请求仍在途：新worker不能按过期就重发，旧执行代次不能提交（Task3、Task5、Task6）。
- 上传正好1038篇或HTM大写后缀：不能触发旧raw固定哈希或丢原编号，producer必须诚实（Task4、Task9）。
- 部分成功/合法零标的/零查询：必须有真实空产物和覆盖状态，不填写不存在文件，不标“全部完成”（Task6–8、Task10–11）。
- 迟到响应及知道他人关系版本hash：不能跨owner、数据集或generation返回原文/图表（Task2、Task9–11）。

## 执行环境、类型与文件图

所有命令在隔离worktree根运行。设置`PYTHONUTF8=1`、`PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`，用已有项目Python执行`python -B`；输出根`TASK3_VERIFY_ROOT`指向仓库之外的已授权本地目录，每次RED/GREEN使用新的basetemp和XML。Node用已有运行时，Vite依赖复用现有匹配lockfile的构建目录；不能把“无node_modules”当构建通过，也不在审批前安装依赖。

新包为`task3_platform/`。`contracts.py`统一定义：`Actor(user_id,role)`、`FileSpec(name,bytes,part_hashes)`、`UploadSpec(files,mode,processing_fingerprint,budget)`、`Job(task_id,upload_dataset_id,owner_id,mode,status,phase,input_fingerprint,processing_fingerprint,epoch)`、`Lease(task_id,epoch,worker_token)`、`PreparedInput(announcement_id,html_path,zip_path,html_sha,zip_sha)`、`Checkpoint(announcement_id,input_fingerprint,processing_fingerprint,result_sha256,db_state)`、`Artifact(name,relative_path,sha256,bytes,kind)`、`ModelBudget(max_calls,max_input_tokens,max_output_tokens)`。Mode为`parse_only/extract`；状态及阶段逐字沿用spec。内部PreparedInput有路径，但转换为web DTO时必须去除；对象不携带可返回浏览器的API key或服务器绝对路径。

模块按职责拆分：`limits.py/paths.py/schema.py/migrate.py` → `auth.py/job_store.py/uploads.py` → `ledger.py/model_transport.py/worker.py/entity_step.py/datasets.py` → `exports.py/relations.py/artifacts.py/web.py`。模型上下文协议放旧包`models/inference.py`，默认算法及旧两参数hook兼容。新测试为`task3_tests/`，前端纯函数测试为`frontend/tests/`，真实环境脚本为`deploy/task3/`。

新增13个表：spec的12个业务表及`t3_runtime_state`单行锁/全局磁盘预留。外键和owner检查不依赖命名猜测；同名公告复合主键，不写旧实体表。Task1同时把`task3_tests`加入pytest的testpaths，注册`mysql_integration`，默认表达式为`not private_data and not mysql_integration`。SQL Fake只证明接口/语句合同，实际锁和持久性在Task12真实验证。

---

### Task 1：合同、限额、目录与迁移

**Files:** Create `task3_platform/{__init__,contracts,limits,paths,schema,migrate}.py`, `task3_tests/{conftest,test_contracts}.py`; Modify `pytest.ini`。
**Interfaces:** `Limits.from_env(mapping)->Limits`（只读白名单配置）；`RuntimePaths(root:Path).task(task_id)->TaskPaths`（received/inputs/extraction/exports四路径）；`migrate(connection, expected_database:str)->MigrationReport`（版本/新增表/复用状态字典）。conftest提供全部合成的`actor_a/actor_b`、`synthetic_html`（ID `t20990101_1`）、`fake_connection`、可注入时钟，默认阻断真实模型/network调用；contracts的`PlatformError(code,http_status)`统一错误合同。

- [ ] RED：`test_limits_and_scoped_paths`断言`assert limits.chunk_bytes==16*1024**2`、`assert limits.job_disk_bytes==12*1024**3`、`assert limits.queue_size==10`；拒绝负配置、`../`、软链接祖先。`test_migration_only_t3_tables`断言DDL只涉及上述13表，无`CREATE DATABASE`/旧表ALTER。
- [ ] Run `python -B -m pytest task3_tests/test_contracts.py -q`，期望因新接口不存在FAIL，不能因真实数据库/模型缺失而失败。
- [ ] 最小实现值、ID格式与目录合同；迁移显式核对目标库与schema版本，不从API启动调用。业务成果/内容指纹编码拒绝float、非有限值、重复key，模型temperature等配置用Decimal文本归一化，仅wire层按provider协议转换；耗时另记整数毫秒。任务ID32hex、上传ID `up_`+32hex、公告ID规范且最多128字符，超限拒绝不截断。
- [ ] GREEN同命令；全基线命令`python -B -m pytest task2_tests task1_entity_extraction/tests deploy/task1-task2 task3_tests -q`零失败，单独保存新测试和基线结果。
- [ ] Commit only新增目录、`pytest.ini`：`feat: task3 scoped contracts and explicit migration`。

### Task 2：平台会话和owner访问控制

**Files:** Create `task3_platform/auth.py`, `task3_tests/test_auth.py`, `deploy/task3/provision_user.py`。
**Interfaces:** `AuthService(connection,clock).provision(username,password,role)->Actor`、`login(username,password,secure_transport)->Session`、`require_actor(session_token)->Actor`、`require_csrf(session,header,origin)->None`、`require_owner(actor,owner_id)->None`。Session含opaque32随机字节令牌、csrf与expires；令牌哈希入库，8小时过期，注销可撤销。

- [ ] RED：`test_private_owner_and_secure_login`断言B不能读A任务、HTTP拒绝账号/费用授权，捕获PlatformError后`assert error.http_status==403`；跨源写403、失效/撤销session401；guest不能获extract权限。`test_password_not_plaintext`断言`assert stored_hash!=supplied_password`且数据库没有原会话令牌。
- [ ] Run `python -B -m pytest task3_tests/test_auth.py -q`，期望FAIL。
- [ ] 用标准库`scrypt`（随机16字节salt、n=32768/r=8/p=1、maxmem64MiB）存密码哈希；用户名NFC、去外部空白、拒控制字符、3–64字符。HttpOnly/Secure/SameSite=Strict cookie；仅信任配置的回环代理来源及被代理覆盖的协议头。guest默认一次单HTML<=1MiB、一个在途parse、每来源IP五次/小时，仅公开演示材料；不能匿名提交私有大包。CLI通过隐藏stdin读密码，不能在命令参数/日志中传密码。
- [ ] GREEN及错误/日志脱敏断言。HTTPS传输测试可用TestClient的HTTPS基址，不冒充真实证书部署。
- [ ] Commit `feat: task3 owner sessions and secure authorization`。

### Task 3：持久任务、领取锁与可恢复检查点

**Files:** Create `task3_platform/job_store.py`, `task3_tests/test_jobs.py`。
**Interfaces:** `JobStore(connection,clock).create_receiving(actor,spec,request_key)->Job`、`finalize(task_id,input_manifest)->Job`、`claim(worker_token)->Lease|None`、`heartbeat(lease)->None`、`checkpoint(lease,aid,result_hash,db_state)->None`、`request_cancel(actor,task_id)->Job`、`recover_expired()->list[Job]`、`transition(lease,status,phase)->None`。

- [ ] RED：`test_single_claim_and_fenced_recovery`断言`assert sum(x is not None for x in claims)==1`、过期epoch写入409、10秒心跳/60秒租约；`test_restart_reuses_confirmed_items`重新创建store仍能读取Job及已提交公告。相同owner/字节/指纹/模式返回原任务，换字节或request_key内容冲突409，换模型/提示词不复用。
- [ ] Run `python -B -m pytest task3_tests/test_jobs.py -q`，期望FAIL。
- [ ] 用`t3_runtime_state`事务锁串行化全局claim与配额预留，排队10后拒绝；单worker持有数据库会话级锁，恢复检查epoch和在途attempt，不因租约过期自动重复模型调用。运行中的staging不可常规查询，状态转换只接受spec允许边。
- [ ] GREEN，Fake断言SQL参数化、取消后不领取新单元；SQL断线、事务回滚和竞争将在Task12实际验证。
- [ ] Commit `feat: persistent task3 queue and fenced checkpoints`。

### Task 4：安全流式上传、分片恢复与配对

**Files:** Create `task3_platform/{uploads,archive_safety}.py`, `task3_tests/test_uploads.py`。
**Interfaces:** `UploadService(store,paths,limits).begin(actor,spec,key)->UploadSession`（Job、服务端file_id/各文件已收part目录）；`put_part(actor,task_id,file_id,part_no,stream)->PartReceipt(file_id,part_no,sha256,bytes,reused)`、`complete(actor,task_id)->Job`；`inspect_archive(path,limits)->ArchiveManifest`（成员、安全类型、大小/总量）；`prepare_inputs(job)->tuple[list[PreparedInput],Diagnostics]`（逐文件code/message） 。

- [ ] RED：`test_chunk_replay_and_conflict`断言`assert replay.reused is True`、写入字节不增加、不同SHA409、16MiB+1拒绝；`test_zip_and_pair_limits`覆盖路径穿越/UNC/盘符/链接/重复basename/坏ZIP/加密/压缩比和实际解压配额。HTM转canonical `.html`字节不变，同stem冲突拒绝，附件缺失明确诊断；非UTF8字节拒绝，不能沿旧`errors=ignore`静默丢字后完成。
- [ ] Run `python -B -m pytest task3_tests/test_uploads.py -q`，期望FAIL。
- [ ] 初始化元数据包含每16MiB分片SHA，浏览器/服务器只按块哈希；实际接收必须核验，完成时计算全文件及清单哈希，幂等不信任客户端声明。按1MiB块保存/解包，所有层配额和剩余空间同时检查；内层附件先预检再交旧解析器。只清理已验证的本任务临时文件，不自动删原输入。
- [ ] GREEN：构造小配额fixture验证8+8仍受12总配额、全局16/预留4、缺分片/磁盘满/重启续传/队列满；不生成数GiB测试文件。
- [ ] Commit `feat: bounded resumable dataset uploads`。

### Task 5：模型transport、调用账本和uncertain传播

**Files:** Create `task3_platform/{ledger,model_transport}.py`, `task1_entity_extraction/models/inference.py`, `task3_tests/test_inference_ledger.py`; Modify `extractor/llm_extractor.py:45–71,82,134,190,248,253`及`pipeline.py:214–235,523–529`（均在旧后端目录下）。
**Interfaces:** 核心`InferenceRequest(task_id,announcement_id,layer,source_file,chunk_index,provider,model,parameters,messages)`、`InferenceResponse(content,usage,provider_request_id,reported_model)`、`InferenceHalt(code)`；`LedgerTransport(store,policy,backend,counter).__call__(request)->InferenceResponse`；`LedgerStore.reserve(lease,request,input_bound,output_bound)->Attempt`、`mark_sent(attempt_id)->None`、`record_response(attempt_id,response)->None`、`mark_uncertain(attempt_id,code)->None`、`require_clear(task_id)->None`。Attempt为`(attempt_id,request_hash,state)`；counter的`upper_bound(request)->int|None`，未知/未核实provider计数策略拒绝live发送，不将估算当实际usage。

- [ ] RED：`test_timeout_is_one_sent_attempt_and_halts_all_layers`断言`assert backend.sent==1`，基础/summary/detail/extra及future原样传播`InferenceHalt`；`test_received_invalid_json_is_not_resent`断言已存响应可重放、解析失败只记提取失败。缺usage不显示0；预算0、撤销授权、取消、不同模型/chunk策略均有明确行为。
- [ ] Run `python -B -m pytest task3_tests/test_inference_ledger.py -q`，期望FAIL，fixture保证不能连真实provider。
- [ ] Extractor增加可选keyword-only `call_transport=None`，注入时不要求真实key/不先创建SDK；内部带context helper调用transport，未注入时仍以两参数调用旧`_call_llm`，保留现有测试hook。注入模式JSON数字用Decimal解析，避免长金额先经float丢精度；旧路径不变。新transport显式关闭SDK隐藏重试，每次发送前事务预留调用/输入上界/输出max_tokens，120秒timeout，持久完整响应/usage；uncertain立即停止，不经旧三次重试或broad catch吞掉。
- [ ] GREEN并跑旧`test_detail_extraction.py`和双层回归；response合法但解析错、缺usage、发送前失败、发送后失联分别验收。调用cache键含上下文、provider/model、所有请求参数、prompt/规则/chunk策略，额外字段层不能每次恢复又收费。
- [ ] Commit `feat: ledger-controlled inference and explicit halt propagation`。

### Task 6：worker状态机与隔离实体处理

**Files:** Create `task3_platform/{worker,entity_step,parse_child}.py`, `task3_tests/test_worker.py`; Modify `task1_entity_extraction/pipeline.py:86–114,190,271–303,695–779`，加入兼容注入的writer、progress_path和prepared parser factory。
**Interfaces:** `ProcessingWorker(jobs,steps,clock).run_once()->Job|None`、`run_parse_child(prepared,limits)->ParsedAnnouncement`、`run_entity_step(lease,prepared,parsed,transport,paths)->EntityStepResult`。本任务定义ParsedAnnouncement为`(content:AnnouncementContent,attachments:AttachmentParseOutcome,input_fingerprint)`，EntityStepResult为`(result:ExtractionResult,diagnostics:dict,stage_status)`；无密码。Core pipeline新增keyword-only `writer=None,progress_path=None,call_transport=None,html_parser_factory=None,attachment_parser_factory=None`，factory返回预解析结果，不重复做OCR。

- [ ] RED：`test_worker_parse_only_and_complete_are_distinct`断言`assert job.status=='validated_only' and saved_entities==0`；`test_resume_no_shared_outputs_or_extra_call`断言逐公告恢复不写公共progress/checkpoint、不调用旧DataRepository，extra也从账本恢复。`test_timeout_cancel_partial_zero`覆盖180秒解析期限、6小时总期限、取消、部分附件失败和合法零标的。
- [ ] Run `python -B -m pytest task3_tests/test_worker.py -q`，期望FAIL。
- [ ] parent保持10秒心跳与全局锁，公告串行；解析在有期限、禁外部网络/模型/数据库的子进程执行，继承现有PDF抽页/OCR但不复制模型调用，所有Python子进程用`-B`。Task3明确`db_enabled=False`、max_workers=1；常规旧入口默认值不变。Task3新raw把非业务耗时归一为整数毫秒/Decimal文本后保存，七字段不变；不对旧冻结raw重写。先persist结果/账本，再调用Task7以同事务保存实体和checkpoint；取消或halt停止后续公告/块。
- [ ] GREEN，检查所有阶段均有持久结果；Fake child/clock测试不是Linux进程资源证明。数据库失败、extra失败、附件提取失败不能只看基础`success`就发布completed；缺附件/原文未披露值不当作算法失败。独立记录JSON/schema有效性，合法空数组与解析错误分别处理；来源已有明确标的而输出空时报告覆盖缺口，不以“空也是合法JSON”掩盖漏提取。
- [ ] Commit `feat: single-worker isolated processing and restart recovery`。

### Task 7：作用域实体仓库与七字段查询

**Files:** Create `task3_platform/{datasets,baseline,filters}.py`, `task3_tests/test_datasets.py`。
**Interfaces:** `DatasetRepository(connection).save_announcement(lease,prepared,result)->Checkpoint`（同事务写公告/实体/明细和`t3_item_checkpoints`） 、`publish(actor,dataset_id,state,manifest)->None`、`search(actor,dataset_id,filters,page,page_size)->Page`、`announcement(actor,dataset_id,aid)->SourceView`；`BaselineAdapter(repo,assets)`只读映射`official`。Page为`(items,total,page,page_size,upload_dataset_id,availability)`；SourceView为脱敏公告/原文/附件目录及来源hash。`EntityFilters`含7字段文本、missing/nonempty、金额/数量上下界，未明确单位不进numeric过滤。

- [ ] RED：`test_same_aid_two_dataset_views`断言A/B同编号的实体/原文/附件分别正确，`assert view_a['source_sha256']!=view_b['source_sha256']`；`test_all_seven_filters_keep_precision`逐字段验证、超精度raw保留、费率/预算不当成交、含单位数量不丢单位。owner违规403、staging409、offset>=10000拒绝，SQL注入/LIKE通配符按字面处理。
- [ ] Run `python -B -m pytest task3_tests/test_datasets.py -q`，期望FAIL。
- [ ] SQL只写`t3_`，同公告基础/明细在事务内替换，保留不同完整页来源；价格规范列用DECIMAL(65,30)，超限raw-only，失败回滚不checkpoint。官方adapter使用旧表SELECT/冻结资产，不写新官方副本或旧表，不改变旧接口默认关键词语义。
- [ ] GREEN，真实SQL隔离/回滚在Task12；部分成功预览携带partial、覆盖/金额资格提示，范围解析不做单价乘数量。
- [ ] Commit `feat: scoped entity storage and seven-field search`。

### Task 8：独立标准三表、失败报告及安全下载

**Files:** Create `task3_platform/{exports,export_child,artifacts}.py`, `task3_tests/test_exports.py`; Reuse纯函数`tools/task1_standard_export/{standard_export,source_name_join,source_item_packages,price_projection}.py`。
**Interfaces:** `build_exports(job,inputs,raw_path,destination)->tuple[list[Artifact],ExportReport]`、`ArtifactService(store,paths).resolve(actor,dataset_id,name)->VerifiedFile`。Price loader只读当前输入，不调用固定R9 loader/旧run_candidate；按实际HTML字节构造parse_source/project/唯一自身列单位投影。

- [ ] RED：`test_saved_three_tables_exact_decimals_and_empty`读回OOXML断言`assert sheet_names==['总表','标的表','投标人表']`、`assert price_v=='1.23000000'`，URL灰色、特殊数量单位、零行合法；公式注入文本不执行。`test_download_scope_and_tamper`断言真实文件哈希、不存在/被改文件拒绝，../与别的owner不能下载。
- [ ] Run `python -B -m pytest task3_tests/test_exports.py -q`，期望FAIL。
- [ ] 生成前按技能读取Excel工具说明；使用独立导出子进程，避免旧offline网络禁用影响API。新portable writer直接写规范OOXML数字字符串，Decimal不经float；读取保存ZIP逐单元格/类型校验并注册hash，errors.json/CSV逐公告列完整。不import有顶层私有argv副作用的旧workbook验收脚本，不复用固定官方XLSX。
- [ ] GREEN，保存文件独立读回和截图抽检是额外真实文件验收，不以函数返回路径替代。
- [ ] Commit `feat: verified task-specific standard exports and downloads`。

### Task 9：上传候选profile与离线关系版本

**Files:** Create `task3_platform/{relations,relation_child}.py`, `task3_tests/test_relations.py`; Modify `task2_relationship_analysis/{builder,inputs,publication}.py`仅profile/producer接口、`task2_query_service/api.py`增加可选router前缀及轻量摘要/覆盖分页（默认旧接口兼容）。
**Interfaces:** `build_dataset(..., expected_count=None, *, input_profile=None)->BuildResult`；`RelationService(connection,paths).build_and_bind(lease,html_root,raw_path,provenance)->RelationBinding`、`repository_for(actor,upload_dataset_id)->MySQLRepository`。新input_profile包含uploaded producer/抽取receipt SHA，并入内容指纹；默认None维持旧验收profile。

- [ ] RED：`test_runtime_1038_does_not_require_v11_hash`、`test_htm_profile_and_producer_not_legacy`、`test_child_boundary_cannot_block_api`；断言`assert manifest['new_model_calls']==0`、`assert manifest['legacy_producer']==profile['producer']`且上游账本独立显示，输入与输出不重叠，重复import同hash复用，未经授权知晓ds hash也403。
- [ ] Run `python -B -m pytest task3_tests/test_relations.py task2_tests -q`，期望新用例FAIL；不要跑1038模型抽取。
- [ ] 子进程输入canonical HTML及同一新raw的两角色，保持事实验证/金额/身份/覆盖算法；子进程退出后父worker验证并import，不调用activate。可信runtime artifact root和owner绑定，不放弃manifest/SQL内容哈希校验。新摘要只含ID/count/资格计数，详情按页<=200，旧status全文仍可兼容访问。
- [ ] GREEN：合成正向五场景、附件-only关系保留未验证、128字符ID边界、runtime source变动拒绝；重新构建的版本变化由impl/profile指纹真实解释，不改旧发布产物。
- [ ] Commit `feat: uploaded relationship profiles and scoped version binding`。

### Task 10：API集成与旧上传安全迁移

**Files:** Create `task3_platform/{web,web_auth,web_uploads,web_datasets}.py`, `task3_tests/test_web.py`; Modify `task1_entity_extraction/api/main.py`、`api/routers/task3.py`、`tests/test_paid_inference_gate.py`及`test_dual_layer_pipeline.py`中的上传计数测试。
**Interfaces:** `install(app,backend_root,*,services_provider=None)->None`；路由提供`auth/login/logout/session`、`uploads`初始化/parts/complete、`tasks`列表/单任务/cancel/retry、`datasets/{id}`实体/来源/附件/产物和`task2/status/organizations/query/evidence`。provider是每请求独立SQL连接，不在import/启动做DDL。

- [ ] RED：`test_legacy_upload_cannot_touch_baseline_even_with_model_flag`断言`assert legacy_repository.writes==0`且旧入口委派隔离服务；`test_private_routes_check_owner_and_relation_binding`覆盖CSRF、transport、partial/staging、所有来源与产物接口，错误响应不含路径/密钥。
- [ ] Run `python -B -m pytest task3_tests/test_web.py task1_entity_extraction/tests/test_paid_inference_gate.py -q`，期望新守卫FAIL。
- [ ] 新功能未迁移/未启用时extract失败关闭，不让旧unsafe路径变fallback；匿名parse保留小公开演示限额，旧GET/tasks改为本人会话的兼容DTO。旧上传计数测试改用新service Fake，继续断言基础1/明细2；明确这是Task3迁移，不改或削弱实体规则测试来凑通过。
- [ ] GREEN及全部默认回归；错误分别403权限、400格式、409冲突/版本、413配额、503未准备/资源。上传HTML按文本展示，原始HTML下载attachment/nosniff，不在页面执行script。API真实TestClient可验证路由，不冒充云端MySQL/HTTPS。
- [ ] Commit `feat: task3 API integration and safe legacy adapters`。

### Task 11：前端任务、数据集、七字段与完全渲染计时

**Files:** Create `frontend/src/{services/datasetContext,services/taskPolling,services/renderMeasurement,services/chunkUpload}.js`、`components/DatasetSelector.vue`、`views/LoginView.vue`、`frontend/tests/{dataset-context,polling,measurement,chunk-upload}.test.js`; Modify `src/api/{client,task1,task2,task3}.js`、`views/{UploadView,EntitiesView,RelationshipsView,StatsView}.vue`、`router/index.js`、`package.json`。
**Interfaces:** `createDatasetContext(initial='official').select(id)/ticket()/accept(ticket,response)`；`createTaskPolling({fetch,schedule,cancelSchedule}).start/stop`；`createRenderMeasurement({now,nextTick,nextFrame,isReady}).start(generation,context)/finish(generation)->Promise<Sample|null>`（Sample含elapsed_ms、generation、dataset_id、query_type）；`hashParts(file,chunkBytes=16MiB)->PartHashes`、`uploadParts(session,files,{onProgress,signal})->Receipt`。

- [ ] RED：Node built-in `node:test`/assert，`test_late_result_after_dataset_switch_is_rejected`断言`assert.equal(ctx.accept(oldTicket,oldResponse),false)`；`test_polling_reconnect_and_unmount`、`test_measurement_waits_table_and_svg_once`、`test_hash_parts_bound_and_resume`断言失败/取消无成功样本，SSR无performance也用注入时钟，不新增Vitest/Jest。
- [ ] Run `node --test frontend/tests/*.test.js`，期望新helper导入FAIL。
- [ ] 纯helper测试后接Vue：URL只保存dataset/task非密钥，账号cookie/CSRF不存密码/key；大包WebCrypto按16MiB分片哈希，HTTP缺安全能力时给明确提示而非一次读4GiB。无重叠async setInterval，刷新从server恢复任务，断线提示与重连；base/new dataset分别走正确作用域。7字段、附件明细与预算/意向边界保留，所有旧generation失效。
- [ ] GREEN Node测试；在匹配lockfile构建副本执行`npm run build`退出0，记录实际bundle。查询返回后nextTick及绘制机会、当前表/SVG就绪才结束计时；在真实浏览器测五次平均，不把纯JS测试当绘制成功。
- [ ] Commit `feat: task3 dataset workflow and full-render measurements`。

### Task 12：真实环境验收、部署与交付

**Files:** Create `deploy/task3/{prepare_acceptance,finalize_release,preflight}.py`、`procurement-worker.service`、`task3_tests/test_deployment.py`、`task3_tests/test_mysql_integration.py`、`deploy/task3/browser_acceptance.cjs`、`docs/task3-operations.md`。
**Interfaces:** `preflight(config)->EnvironmentReport`（secret白名单不回显）；`prepare_acceptance(release,target_config)->AcceptanceReport`；`finalize_release(acceptance,expected_old_manifest)->CutoverReport`。真实MySQL测试只读显式`TASK3_MYSQL_TEST_DB`，名称须以`procurement_task3_accept_`开头，未配置不连生产。

- [ ] RED：`test_deploy_refuses_live_target_missing_tls_or_browser_gate`、`test_worker_unit_and_no_startup_ddl`、真实SQL测试同名双数据集/竞争领取/重启/回滚/owner；验收报告要求`assert report['old_dataset_fingerprint_unchanged'] is True`。服务单worker、MemoryMax1024MiB（worker及孩子总cgroup）、期限/停止策略明确；不宣称Windows Fake能证明Linux限额。
- [ ] Run默认部署守卫，期望FAIL；真实SQL用`python -B -m pytest task3_tests/test_mysql_integration.py -m mysql_integration -q`，只有隔离授权和配置后才运行，不把skip当pass。
- [ ] 管理员准备隔离库、HTTPS/账号及服务安装前置；先Fake backend真实SQL+浏览器闭环，再在另行授权下最多3篇样本、最多8次模型请求、有限token预算验证真实抽取。初始预算示例input64000/output32768仅在已核实counter/profile及明确授权后启用，不据此自动获得费用权限。输入原文不上传GitHub。
- [ ] GREEN：官方基准指纹前后不变；五场景完全渲染均值、冷/热、3用户加处理、API/worker峰值及系统available/swap均有报告。账号真实安全传输、上传分片/恢复/结果下载/原文/附件/选择切换/部分失败/预算拒绝通过；浏览器恶意script合成来源不执行。任何外部前置未满足明确未验收。发布前一次新Task3全分支独立代码审查，Native主代理TDD修复重要问题，保存新回归；切换只在真实SQL/GUI/manifest/备份守卫通过后由管理员执行，再公网复验。
- [ ] Commit脱敏代码/合成测试/操作文档；沿用干净分支提交PR，不包含私有祖先或产物，创建后attach。最终集成遵守已授权范围，不强推或删除本地用户资料；交付实际覆盖与未达项，不宣传官方精确率100%。

## 批次检查点与完成证据

任务1–5完成第一批持久隔离及输入/费用基础；任务6–10完成后台闭环；任务11完成平台交互；任务12完成实际部署验收。每批保存RED/GREEN、commit及剩余项，不能因某个单测绿了就宣称任务三结束。

所有单任务先写列出的失败测试、运行并读取真正失败原因，再实现、GREEN、回归、显式文件commit。除真实SQL/浏览器/模型特定步骤外，本地默认测试均用合成样本/Fake，不接真实API/生产数据库。未授权小样本、HTTPS或管理员部署均是实际验收门槛；不阻止完成其他本地实现，但阻止最终“全部完成”声明。

## 主代理计划自查与执行交接

spec1–3对应Task1/3/7/10；spec4对应Task4/11/12；spec5对应Task3/5/6；spec6对应Task2/5/6；spec7对应Task6–9；spec8对应Task7/9–11；spec9对应Task2/10/12；spec10对应Task6/9/11/12；spec11–12对应全部回归及Task12。Review Focus五类均已分配具体测试。所有跨任务对象来自contracts或核心inference协议，model response/ledger状态与抽取结果分别记录，不混用。

当前只提交设计和计划，产品代码尚未实现。用户已确认书面设计；保留Native偏好。计划审阅确认后，使用executing-plans开始Task1，再逐项执行，不再次选择架构；真实费用与云端特权不包含在计划确认中。
