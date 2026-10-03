"""
任务一：主流程编排
整合数据解析 → 实体提取 → 结果输出（文件 + 数据库）的完整流程
"""
import os
import time
import warnings
import logging
import threading
from pathlib import Path
from typing import List, Optional
from concurrent.futures import ThreadPoolExecutor, as_completed

from tqdm import tqdm

from data_loader.html_parser import HTMLAnnouncementParser
from data_loader.attachment_parser import AttachmentParser
from extractor.llm_extractor import LLMExtractor
from output.writer import ResultWriter
from evaluation.metrics import Evaluator
from models.schemas import ExtractionResult, AnnouncementContent
import config

# 抑制 PDF 解析等第三方库的烦冗警告
warnings.filterwarnings("ignore")
logging.getLogger("pdfplumber").setLevel(logging.ERROR)
logging.getLogger("pdfminer").setLevel(logging.ERROR)


class EntityExtractionPipeline:
    """实体提取完整流水线"""

    def __init__(
        self,
        llm_provider: str = None,
        api_key: str = None,
        base_url: str = None,
        model: str = None,
        extract_extra: bool = True,
        db_enabled: bool = None,
        max_workers: int = 8,
    ):
        self.html_parser = HTMLAnnouncementParser()
        self.extractor = LLMExtractor(
            provider=llm_provider,
            api_key=api_key,
            base_url=base_url,
            model=model,
        )
        self.writer = ResultWriter()
        self.evaluator = Evaluator()
        self.extract_extra = extract_extra  # 是否同时提取任务二需要的额外字段
        self.max_workers = max_workers  # 并发线程数
        # pymysql 连接非线程安全，并发处理时需串行化数据库写入
        self._db_lock = threading.Lock()

        # 数据库初始化
        self.db_enabled = db_enabled if db_enabled is not None else config.DB_ENABLED
        self.db = None
        self.repository = None
        if self.db_enabled:
            self._init_database()

    def _init_database(self):
        """初始化数据库连接（失败时降级为仅文件输出）"""
        try:
            from database.connection import DatabaseManager, DatabaseConfig
            from database.dao import DataRepository

            db_config = DatabaseConfig(
                host=config.DB_HOST,
                port=config.DB_PORT,
                user=config.DB_USER,
                password=config.DB_PASSWORD,
                database=config.DB_NAME,
            )
            self.db = DatabaseManager(db_config).connect(create_db=True)
            self.db.init_tables()
            self.repository = DataRepository(self.db)
            print("[Pipeline] 数据库已就绪，提取结果将同时写入 MySQL")
        except Exception as e:
            print(f"[Pipeline警告] 数据库连接失败，将仅输出到文件: {e}")
            self.db_enabled = False
            self.db = None
            self.repository = None

    def process_directory(self, dir_path: str, skip_ids: set = None,
                          progress_callback=None,
                          checkpoint_results: List[ExtractionResult] = None) -> List[ExtractionResult]:
        """
        批量处理一个目录下的所有公告（并发处理）
        目录结构：每个公告一个子目录，包含 .html 文件和附件 zip（如有）

        Args:
            skip_ids: 需要跳过的公告 ID 集合（用于断点续跑）
            progress_callback: 可选回调 fn(done, total, success_count, result)，
                               供 API 层上报任务进度
            checkpoint_results: 已加载的历史成功结果；写检查点时与本轮结果合并
        """
        import sys
        dir_path = Path(dir_path)
        if not dir_path.exists():
            raise FileNotFoundError(f"目录不存在: {dir_path}")

        # 查找所有 HTML 文件
        html_files = sorted(
            list(dir_path.rglob("*.html")) + list(dir_path.rglob("*.htm")),
            key=lambda p: str(p).lower(),
        )
        if skip_ids:
            before = len(html_files)
            html_files = [f for f in html_files if f.stem not in skip_ids]
            print(f"[续跑] 跳过已成功处理的 {before - len(html_files)} 个公告，剩余 {len(html_files)} 个待处理")
        if not html_files:
            print(f"[警告] 在 {dir_path} 中未找到待处理的 HTML 文件")
            return []

        total = len(html_files)
        print(f"\n[信息] 发现 {total} 个公告 HTML 文件，开始并发处理（{self.max_workers} 线程）...")
        sys.stdout.flush()
        results = []
        start_time = time.time()
        done_count = 0
        success_count = 0
        progress_file = config.OUTPUT_DIR / "progress.txt"
        checkpoint_results = list(checkpoint_results or [])

        # 并发处理
        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            futures = {
                executor.submit(self.process_single_file, str(html_file)): html_file
                for html_file in html_files
            }
            for future in as_completed(futures):
                html_file = futures[future]
                try:
                    result = future.result()
                    results.append(result)
                    if result.success:
                        success_count += 1
                except Exception as e:
                    print(f"\n[错误] 处理 {html_file.name} 失败: {e}")
                    sys.stdout.flush()
                    result = ExtractionResult(
                        announcement_id=html_file.stem,
                        success=False,
                        error_message=str(e),
                    )
                    results.append(result)
                done_count += 1
                if progress_callback:
                    try:
                        progress_callback(done_count, total, success_count, result)
                    except Exception:
                        pass
                # 每 10 个文件写一次进度
                if done_count % 10 == 0 or done_count == total:
                    self._write_checkpoint(checkpoint_results + results)
                    elapsed = time.time() - start_time
                    rate = done_count / elapsed if elapsed > 0 else 0
                    eta = (total - done_count) / rate if rate > 0 else 0
                    progress_info = (
                        f"进度: {done_count}/{total} ({done_count*100//total}%) | "
                        f"成功: {success_count} | 耗时: {elapsed:.0f}s | "
                        f"速度: {rate:.2f}/s | 预计剩余: {eta:.0f}s"
                    )
                    print(f"\r{progress_info}", end="", flush=True)
                    with open(progress_file, "w", encoding="utf-8") as f:
                        f.write(progress_info + "\n")
                        f.write(f"已完成文件列表（前20）:\n")
                        for r in results[-20:]:
                            status = "OK" if r.success else "FAIL"
                            f.write(f"  [{status}] {r.announcement_id}: {len(r.entities)} entities\n")

        elapsed = time.time() - start_time
        print(f"\n[信息] 处理完成！共 {len(results)} 篇，成功 {success_count} 篇，"
              f"耗时 {elapsed:.1f}s，平均 {elapsed / len(results):.2f}s/篇")
        sys.stdout.flush()

        return results

    def process_single_file(self, html_file_path: str) -> ExtractionResult:
        """处理单个公告文件（HTML + 同目录附件）"""
        html_path = Path(html_file_path)
        announcement_id = html_path.stem

        # 1. 解析 HTML
        content = self.html_parser.parse_file(str(html_path))

        # 2. 查找并解析附件（仅匹配与 HTML 同名的 zip 文件）
        matching_zip = html_path.parent / f"{announcement_id}.zip"
        if matching_zip.exists():
            # AttachmentParser 会记录本次解析状态；并发任务各用独立实例，
            # 避免多个线程共享可变列表造成统计串扰。
            attachment_parser = AttachmentParser()
            texts, tables = attachment_parser.parse_zip(str(matching_zip), announcement_id)
            content.attachment_texts.update(texts)
            content.attachment_tables.update(tables)
            content.attachment_files.extend(dict.fromkeys([*texts.keys(), *tables.keys()]))

        # 3. 实体提取
        has_tables = bool(content.html_tables) or bool(content.attachment_tables)
        result = self.extractor.extract(
            text=content.full_text,
            has_tables=has_tables,
            announcement_id=announcement_id,
        )

        # 4. 提取额外信息（任务二需要）
        if self.extract_extra and result.success:
            extra = self.extractor.extract_extra_info(content.full_text, announcement_id)
            if extra and "error" not in extra:
                result.extra_info = extra

        # 5. 写入数据库
        if self.repository:
            try:
                with self._db_lock:
                    self.repository.save_extraction_result(
                        result=result,
                        title=content.title,
                        raw_text=content.full_text,
                        file_path=str(html_path),
                    )
            except Exception as e:
                print(f"\n  [数据库警告] 写入 {announcement_id} 失败: {e}")

        return result

    def run(
        self,
        input_path: str,
        output_filename: str = None,
        output_format: str = None,
        ground_truth_path: str = None,
        resume: bool = False,
    ):
        """
        运行完整流水线

        Args:
            input_path: 输入目录或单个 HTML 文件路径
            output_filename: 输出文件名（不含扩展名）
            output_format: 输出格式 csv/excel/json
            ground_truth_path: 标准答案 JSON 路径（用于评测）
            resume: 断点续跑，跳过已成功处理的公告并合并历史结果
        """
        path = Path(input_path)

        # 断点续跑：加载已有成功结果并跳过
        prev_results = []
        skip_ids = set()
        if resume:
            prev_results, skip_ids = self._load_previous_results()
            print(f"[续跑] 已加载历史成功结果 {len(prev_results)} 条")

        # 处理输入
        if path.is_dir():
            results = self.process_directory(
                str(path),
                skip_ids=skip_ids,
                checkpoint_results=prev_results,
            )
        elif path.suffix.lower() in (".html", ".htm"):
            if path.stem in skip_ids:
                print(f"[续跑] {path.name} 已成功处理过，跳过")
                results = []
            else:
                print(f"\n[信息] 处理单个文件: {path.name}")
                results = [self.process_single_file(str(path))]
        else:
            raise ValueError(f"不支持的输入: {input_path}，请提供目录或 HTML 文件")

        # 合并历史成功结果 + 本次结果
        all_results = prev_results + results

        # 输出结果
        if all_results:
            self.writer.write(all_results, filename=output_filename, fmt=output_format)
            self.writer.write_raw_results(all_results)
            self._clear_checkpoint()

            # 统计
            success_count = sum(1 for r in all_results if r.success)
            total_entities = sum(len(r.entities) for r in all_results)
            print(f"\n[统计] 成功: {success_count}/{len(all_results)}，"
                  f"共提取 {total_entities} 个标的物")

            # 数据库统计
            if self.repository:
                try:
                    db_count = self.repository.entities.count_all()
                    print(f"[数据库] 已入库 {db_count} 条标的物记录")
                except Exception:
                    pass

            # 评测（如果有标准答案）
            if ground_truth_path and Path(ground_truth_path).exists():
                import json
                with open(ground_truth_path, "r", encoding="utf-8") as f:
                    ground_truths = json.load(f)
                metrics = self.evaluator.evaluate(all_results, ground_truths)
                self.evaluator.print_report(metrics)

        # 关闭数据库连接
        if self.db:
            self.db.close()

        return all_results

    def _load_previous_results(self) -> tuple:
        """
        加载上次运行的成功结果（用于断点续跑）

        Returns:
            (成功结果列表, 已成功的公告ID集合)
        """
        import json
        from models.schemas import ExtractedEntity, deduplicate_entities

        writer = getattr(self, "writer", None)
        output_dir = Path(getattr(writer, "output_dir", config.OUTPUT_DIR))
        checkpoint_path = output_dir / "raw_results.checkpoint.json"
        raw_path = checkpoint_path if checkpoint_path.exists() else output_dir / "raw_results.json"
        if not raw_path.exists():
            print(f"[续跑警告] 未找到历史结果文件 {raw_path}，将全量处理")
            return [], set()

        if raw_path == checkpoint_path:
            print(f"[续跑] 检测到中断检查点，将优先读取 {checkpoint_path}")

        with open(raw_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        prev_results = []
        skip_ids = set()
        for item in data:
            if not item.get("success"):
                continue
            entities = deduplicate_entities(
                [ExtractedEntity(**e) for e in item.get("entities", [])]
            )
            prev_results.append(ExtractionResult(
                announcement_id=item["announcement_id"],
                entities=entities,
                extra_info=item.get("extra_info") or {},
                success=True,
            ))
            skip_ids.add(item["announcement_id"])

        return prev_results, skip_ids

    def _write_checkpoint(self, results: List[ExtractionResult]):
        """原子写入中断检查点，避免进程退出时破坏已有进度。"""
        writer = getattr(self, "writer", None)
        output_dir = Path(getattr(writer, "output_dir", config.OUTPUT_DIR))
        output_dir.mkdir(parents=True, exist_ok=True)
        temp_name = "raw_results.checkpoint.tmp.json"
        temp_path = output_dir / temp_name
        checkpoint_path = output_dir / "raw_results.checkpoint.json"
        (writer or ResultWriter(output_dir)).write_raw_results(results, filename=temp_name)
        os.replace(temp_path, checkpoint_path)

    def _clear_checkpoint(self):
        """正式结果写入成功后清理临时检查点。"""
        writer = getattr(self, "writer", None)
        output_dir = Path(getattr(writer, "output_dir", config.OUTPUT_DIR))
        checkpoint_path = output_dir / "raw_results.checkpoint.json"
        checkpoint_path.unlink(missing_ok=True)
