# 阿里云 2 核 4 GB 部署运行手册

> 最后验证：2026-10-03
> 目标系统：Ubuntu 24.04，2 vCPU，约 3.4 GiB 可见内存
> 公网入口：`http://<SERVER_IP>/`（请在本地部署记录中填写真实地址，不要提交公开仓库）

## 1. 已部署架构

```text
公网 :80
   │
   ▼
Nginx ──静态文件──> /srv/procurement-app/current/frontend-dist
   │
   └─ /api/* ──> Uvicorn 127.0.0.1:8000（1 worker）
                         │
                         ▼
                  MySQL 127.0.0.1:3306
```

- Nginx、MySQL、`procurement-api.service` 均由 systemd 管理并开机自启。
- 应用进程使用普通用户 `zhoujin:projectteam`，`zhoujin` 不授予长期 sudo。
- 对公网只开放 SSH 22 和 HTTP 80；8000、3306 只监听回环地址。
- 生产环境变量保存在 `/etc/procurement-app/procurement.env`，权限必须保持 `0640 root:projectteam`。
- 环境文件仅记录变量值，不得复制进 Git、发布 ZIP、比赛材料或日志。

## 2. 路径和服务

| 项目 | 路径或名称 |
|------|------------|
| 应用根目录 | `/srv/procurement-app` |
| 版本目录 | `/srv/procurement-app/releases/<release-id>` |
| 当前版本 | `/srv/procurement-app/current`（原子软链接） |
| 共享虚拟环境 | `/srv/procurement-app/shared/venv` |
| 生产环境文件 | `/etc/procurement-app/procurement.env` |
| 后端服务 | `procurement-api.service` |
| Nginx 站点 | `/etc/nginx/sites-available/procurement-app` |
| MySQL 低内存配置 | `/etc/mysql/mysql.conf.d/99-procurement-low-memory.cnf` |

常用检查命令：

```bash
systemctl is-active nginx mysql procurement-api
systemctl is-enabled nginx mysql procurement-api
curl --fail http://127.0.0.1/api/health
ss -lnt | grep -E '(:22|:80|:3306|:8000)([[:space:]]|$)'
free -h
df -h /
```

查看日志：

```bash
journalctl -u procurement-api -n 100 --no-pager
journalctl -u procurement-api -f
sudo tail -n 100 /var/log/nginx/error.log
sudo journalctl -u mysql -n 100 --no-pager
```

## 3. 当前实测基线

| 检查项 | 结果 |
|--------|------|
| 公网首页 | HTTP 200；入口 HTML、JS、CSS 均可读取 |
| 公网健康接口 | HTTP 200；数据库连接正常 |
| 数据库公告 | 1,038 |
| 原始 JSON 实体行 | 6,856 |
| 数据库存储实体 | 6,793（去除 63 条七字段完全重复记录） |
| 项目关系 | 1,027 |
| 投标人 | 3,621 |
| 重复导入 | 两次导入后四表计数保持不变 |
| 空闲内存基线 | 使用约 733 MiB，可用约 2.7 GiB |
| Swap | 2 GiB，验收时使用 0 B |
| 磁盘 | 49 GiB，总剩余约 41 GiB |
| API 进程 | 单 worker；systemd 统计约 42 MiB，进程 RSS 约 71 MiB |
| 故障演练 | 后端停止时前端 200、API 502；恢复后 API 200 |

首次部署只有一个版本，因此没有伪造“切回上一版本”的回滚结果。产生第二个完整版本后再按第 6 节执行真实回滚演练。

## 4. 构建和升级

在 Windows 项目根目录构建经过审计的发布包：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File deploy\build-release.ps1
```

脚本会执行前端生产构建，并拒绝 `.env`、SSH 私钥、`node_modules`、`__pycache__`、`.git`、上传目录和 MySQL 数据目录。ZIP 内路径必须使用 Linux 可识别的 `/`。

上传后先比较本地输出与服务器的 SHA-256：

```bash
sha256sum /home/zhoujin/deployment/procurement-app-<release-id>.zip
```

以 `zhoujin` 安装新版本：

```bash
release_id=<release-id>
release_dir=/srv/procurement-app/releases/$release_id
mkdir -p "$release_dir"
unzip -q /home/zhoujin/deployment/procurement-app-$release_id.zip -d "$release_dir"

/srv/procurement-app/shared/venv/bin/pip install --no-cache-dir \
  -r "$release_dir/backend/requirements.txt"
cd "$release_dir/backend"
PYTHONUTF8=1 /srv/procurement-app/shared/venv/bin/python test_framework.py
```

测试通过后原子切换：

```bash
ln -s "$release_dir" /srv/procurement-app/current.new
mv -Tf /srv/procurement-app/current.new /srv/procurement-app/current
```

加载生产环境并幂等导入现有结构化结果：

```bash
set -a
. /etc/procurement-app/procurement.env
set +a
cd /srv/procurement-app/current/backend
/srv/procurement-app/shared/venv/bin/python import_to_db.py
unset DB_PASSWORD DEEPSEEK_API_KEY QWEN_API_KEY
```

最后由 `admin` 执行：

```bash
sudo bash /srv/procurement-app/current/deploy/finalize-services.sh
```

## 5. 启停与健康检查

服务状态变更由 `admin` 执行：

```bash
sudo systemctl restart procurement-api
sudo systemctl restart nginx
sudo systemctl restart mysql
sudo systemctl stop procurement-api
sudo systemctl start procurement-api
```

每次变更后至少验证：

```bash
curl --fail http://127.0.0.1/api/health
curl --fail http://<SERVER_IP>/api/health
systemctl --no-pager --full status procurement-api
```

## 6. 回滚

先查看可用版本和当前目标：

```bash
ls -la /srv/procurement-app/releases
readlink -f /srv/procurement-app/current
```

只选择已经完成依赖安装与服务器测试的版本：

```bash
previous=/srv/procurement-app/releases/<previous-release-id>
ln -s "$previous" /srv/procurement-app/current.rollback
mv -Tf /srv/procurement-app/current.rollback /srv/procurement-app/current
sudo systemctl restart procurement-api nginx
curl --fail http://127.0.0.1/api/health
```

数据库结构或数据格式发生不兼容变化时，不能只切软链接；必须先恢复匹配版本的数据库备份。

## 7. 数据库备份

由 `admin` 使用本机 socket 备份，不在命令行写应用密码：

```bash
sudo install -d -m 0700 /srv/procurement-app/backups
sudo sh -c 'mysqldump --protocol=socket --single-transaction --routines --triggers procurement_kg | gzip -c > /srv/procurement-app/backups/procurement_kg-$(date +%F-%H%M%S).sql.gz'
```

恢复前先停止后端并再次确认备份文件：

```bash
sudo systemctl stop procurement-api
sudo sh -c 'gunzip -c /srv/procurement-app/backups/<backup>.sql.gz | mysql --protocol=socket procurement_kg'
sudo systemctl start procurement-api
curl --fail http://127.0.0.1/api/health
```

## 8. 2 核 4 GB 资源约束

- Uvicorn 固定 1 个 worker。
- 提取与附件分析默认 `PROCESS_MAX_WORKERS=2`。
- RapidOCR 惰性加载并串行推理；单 PDF 默认最多 OCR 5 页。
- MySQL `innodb_buffer_pool_size=256M`、`max_connections=30`。
- 2 GiB Swap 仅作峰值保护，不能代替内存监控。
- 首阶段不上传 3.5 GB 原始附件；服务器只部署代码、前端和结构化结果。
- 典型查询后应检查 OOM、服务重启次数和磁盘剩余；磁盘低于 10 GiB 时停止上传大数据。

## 9. 数据与评测口径

- 附件解析覆盖报告已经对 1,943 个附件全量重跑：成功解析 1,846 个，附件可用公告 820/1,038。
- 当前 6,856 条实体结果生成于最新 OCR/长 PDF/Prompt 修复之前，尚未用新版链路全量重新提取；部署不能宣称这些实体已经享受全部新修复。
- 旧的“100% 准确率”来自未完成人工核验的验证集，不能作为比赛准确率。必须完成 40 篇人工标注后再报告 P/R/F1。
- 本次部署只导入现有 `raw_results.json`，没有在服务器调用外部大模型。

## 10. 凭据安全

环境文件可能包含以下敏感变量，只记录变量名，不记录值：

```text
DB_PASSWORD
DEEPSEEK_API_KEY
QWEN_API_KEY
```

- SSH 密码曾出现在聊天中，部署后必须立即由用户本人修改；自动化不得代替用户提交新密码。
- 更换密码后优先改为每人独立 SSH 密钥，并关闭共享密码做法。
- 定期执行 `id zhoujin` 和 `sudo -n true`，确认普通应用用户没有额外 sudo 权限。
- 权限检查：`stat -c '%a %U:%G %n' /etc/procurement-app/procurement.env` 应返回 `640 root:projectteam`。
