"""
任务一：主流程编排
整合数据解析 → 实体提取 → 结果输出（文件 + 数据库）的完整流程
"""
import hashlib
import json
import time
import warnings
import logging
import threading
from pathlib import Path
from typing import List, Literal, Optional
from concurrent.futures import ThreadPoolExecutor, as_completed

from tqdm import tqdm

from data_loader.html_parser import HTMLAnnouncementParser
from data_loader.attachment_parser import (
    AttachmentParseOutcome,
    AttachmentParser,
    classify_attachment_status,
)
from extractor.llm_extractor import LLMExtractor
from extractor.detail_processing import migrate_legacy_entities
from extractor.summary_rules import build_summary_source
from output.writer import ResultWriter
from evaluation.metrics import Evaluator
from models.schemas import ExtractionResult, AnnouncementContent
from models.result_io import parse_extraction_result
import config


RunMode = Literal["summary", "details", "both"]


def _stable_hash(value) -> str:
    """为可 JSON 化的层输入生成稳定摘要，供断点续跑判断。"""
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _merge_results(*groups: List[ExtractionResult]) -> List[ExtractionResult]:
    """按公告 ID 合并结果，后出现的结果覆盖前者并保持稳定顺序。"""
    ordered_ids = []
    by_id = {}
    for group in groups:
        for result in group or []:
            if result.announcement_id not in by_id:
                ordered_ids.append(result.announcement_id)
            by_id[result.announcement_id] = result
    return [by_id[announcement_id] for announcement_id in ordered_ids]

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

    def process_directory(
        self,
        dir_path: str,
        skip_ids: set = None,
        progress_callback=None,
        checkpoint_results: List[ExtractionResult] = None,
        *,
        run_mode: RunMode = "both",
        previous_results: List[ExtractionResult] = None,
        include_ids: set[str] | None = None,
        resume: bool = False,
        raw_output_filename: str = "raw_results.json",
    ) -> List[ExtractionResult]:
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
        if include_ids is not None:
            html_files = [f for f in html_files if f.stem in include_ids]
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
        previous_by_id = {
            result.announcement_id: result
            for result in (previous_results or [])
        }
        use_layer_arguments = bool(previous_by_id) or resume or run_mode != "both"

        # 并发处理
        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            futures = {}
            for html_file in html_files:
                if use_layer_arguments:
                    future = executor.submit(
                        self.process_single_file,
                        str(html_file),
                        run_mode=run_mode,
                        previous_result=previous_by_id.get(html_file.stem),
                        resume=resume,
                    )
                else:
                    # 保持对旧测试替身及第三方子类的向后兼容。
                    future = executor.submit(self.process_single_file, str(html_file))
                futures[future] = html_file
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
                    checkpoint_data = _merge_results(
                        checkpoint_results,
                        list(previous_by_id.values()),
                        results,
                    )
                    self._write_checkpoint(
                        checkpoint_data,
                        raw_output_filename=raw_output_filename,
                        run_mode=run_mode,
                    )
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

    def process_single_file(
        self,
        html_file_path: str,
        *,
        run_mode: RunMode = "both",
        previous_result: ExtractionResult | None = None,
        resume: bool = False,
    ) -> ExtractionResult:
        """处理单个公告文件（HTML + 同目录附件）"""
        html_path = Path(html_file_path)
        announcement_id = html_path.stem

        # 1. 解析 HTML
        content = self.html_parser.parse_file(str(html_path))

        # 2. 查找并解析附件（仅匹配与 HTML 同名的 zip 文件）
        matching_zip = html_path.parent / f"{announcement_id}.zip"
        parse_outcome: AttachmentParseOutcome | None = None
        if matching_zip.exists():
            # AttachmentParser 会记录本次解析状态；并发任务各用独立实例，
            # 避免多个线程共享可变列表造成统计串扰。
            attachment_parser = AttachmentParser()
            parse_outcome = attachment_parser.parse_zip_with_status(
                str(matching_zip),
                announcement_id,
            )
            content.attachment_texts.update(parse_outcome.texts)
            content.attachment_tables.update(parse_outcome.tables)
            content.attachment_files.extend(parse_outcome.archive_files)

        detail_status = classify_attachment_status(
            content.html_text,
            matching_zip.exists(),
            parse_outcome,
        )

        # 兼容旧版自定义提取器：它仍接收 HTML 与附件合并后的单层输入。
        if not hasattr(self.extractor, "extract_summary"):
            result = self.extractor.extract(
                text=content.full_text,
                has_tables=bool(content.html_tables) or bool(content.attachment_tables),
                announcement_id=announcement_id,
            )
            result.detail_status = detail_status
            if parse_outcome:
                result.detail_error_message = parse_outcome.error_message
            return self._finish_single_result(result, content, html_path)

        original_previous = previous_result.model_copy(deep=True) if previous_result else None
        previous_is_verified_v2 = bool(
            original_previous
            and original_previous.schema_version >= 2
            and original_previous.success
        )
        if previous_result:
            result = migrate_legacy_entities(
                previous_result.model_copy(deep=True),
                detail_status=detail_status,
            )
        else:
            result = ExtractionResult(
                announcement_id=announcement_id,
                detail_status=detail_status,
                success=(run_mode != "details"),
            )
        result.announcement_id = announcement_id
        result.schema_version = 2
        result.detail_status = detail_status

        # 3. 基础层：严格只使用 HTML，附件正文永不进入该提示词。
        summary_source = build_summary_source(content.html_text, content.html_tables)
        summary_hash = _stable_hash(
            {"text": summary_source, "detail_status": detail_status}
        )
        if run_mode in {"summary", "both"}:
            summary_is_current = bool(
                resume
                and previous_is_verified_v2
                and original_previous.summary_input_hash == summary_hash
                and original_previous.entities
            )
            if summary_is_current:
                result.entities = original_previous.entities
                result.success = True
                result.error_message = original_previous.error_message
            else:
                summary = self.extractor.extract_summary(
                    summary_source,
                    has_tables=bool(content.html_tables),
                    detail_status=detail_status,
                    announcement_id=announcement_id,
                )
                if summary.success:
                    result.entities = summary.entities
                    result.success = True
                    result.error_message = ""
                elif previous_is_verified_v2 and original_previous.entities:
                    result.entities = original_previous.entities
                    result.success = True
                    result.error_message = (
                        f"summary rerun failed: {summary.error_message}"
                    )
                else:
                    result.entities = []
                    result.success = False
                    result.error_message = summary.error_message
            result.summary_input_hash = summary_hash

        # 4. 明细层：只在附件可读时调用，失败不改变基础层 success。
        detail_hash = _stable_hash(
            {
                "texts": content.attachment_texts,
                "tables": content.attachment_tables,
                "status": detail_status,
            }
        )
        parser_error = parse_outcome.error_message if parse_outcome else ""
        if run_mode in {"details", "both"} and detail_status == "available":
            details_are_current = bool(
                resume
                and original_previous
                and original_previous.schema_version >= 2
                and original_previous.detail_status == "available"
                and original_previous.detail_input_hash == detail_hash
                and original_previous.detail_entities
            )
            if details_are_current:
                result.detail_entities = original_previous.detail_entities
                result.detail_status = "available"
                result.detail_error_message = original_previous.detail_error_message
            else:
                details, extracted_status, extraction_error = self.extractor.extract_details(
                    texts=content.attachment_texts,
                    tables=content.attachment_tables,
                    announcement_id=announcement_id,
                )
                if extracted_status == "available":
                    result.detail_entities = details
                    result.detail_status = "available"
                else:
                    if (
                        original_previous
                        and original_previous.schema_version >= 2
                        and original_previous.detail_status == "available"
                        and original_previous.detail_entities
                    ):
                        result.detail_entities = original_previous.detail_entities
                    else:
                        result.detail_entities = []
                    result.detail_status = "extraction_failed"
                result.detail_error_message = "；".join(
                    message
                    for message in (parser_error, extraction_error)
                    if message
                )
            result.detail_input_hash = detail_hash
        elif run_mode in {"details", "both"}:
            result.detail_entities = []
            result.detail_status = detail_status
            result.detail_error_message = parser_error
            result.detail_input_hash = detail_hash

        # 5. 额外信息沿用既有行为；它不参与任务一两层准确性判定。
        if self.extract_extra and result.success:
            extra = self.extractor.extract_extra_info(content.full_text, announcement_id)
            if extra and "error" not in extra:
                result.extra_info = extra

        return self._finish_single_result(result, content, html_path)

    def _finish_single_result(
        self,
        result: ExtractionResult,
        content: AnnouncementContent,
        html_path: Path,
    ) -> ExtractionResult:
        """执行单篇公告的公共数据库收尾。"""

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
        run_mode: RunMode = "both",
        previous_results_path: str | None = None,
        raw_output_filename: str = "raw_results.json",
        include_ids: set[str] | None = None,
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

        # 历史结果既可用于旧结构迁移，也可用于分层断点续跑。
        prev_results = []
        if previous_results_path:
            prev_results, _ = self._load_previous_results(
                explicit_path=previous_results_path,
                successful_only=False,
            )
            print(f"[历史结果] 已加载 {len(prev_results)} 条")
        elif resume:
            prev_results, _ = self._load_previous_results(
                raw_output_filename=raw_output_filename,
                run_mode=run_mode,
                successful_only=False,
            )
            print(f"[续跑] 已加载历史结果 {len(prev_results)} 条")
        previous_by_id = {result.announcement_id: result for result in prev_results}

        # 处理输入
        if path.is_dir():
            results = self.process_directory(
                str(path),
                checkpoint_results=prev_results,
                run_mode=run_mode,
                previous_results=prev_results,
                include_ids=include_ids,
                resume=resume,
                raw_output_filename=raw_output_filename,
            )
        elif path.suffix.lower() in (".html", ".htm"):
            if include_ids is not None and path.stem not in include_ids:
                print(f"[筛选] {path.name} 不在公告 ID 清单中，跳过")
                results = []
            else:
                print(f"\n[信息] 处理单个文件: {path.name}")
                results = [
                    self.process_single_file(
                        str(path),
                        run_mode=run_mode,
                        previous_result=previous_by_id.get(path.stem),
                        resume=resume,
                    )
                ]
        else:
            raise ValueError(f"不支持的输入: {input_path}，请提供目录或 HTML 文件")

        # 合并历史成功结果 + 本次结果
        all_results = _merge_results(prev_results, results)

        # 输出结果
        if all_results:
            self.writer.write(all_results, filename=output_filename, fmt=output_format)
            self.writer.write_raw_results(all_results, filename=raw_output_filename)
            self._clear_checkpoint(
                raw_output_filename=raw_output_filename,
                run_mode=run_mode,
            )

            # 统计
            success_count = sum(1 for r in all_results if r.success)
            total_entities = sum(len(r.entities) for r in all_results)
            total_details = sum(len(r.detail_entities) for r in all_results)
            print(f"\n[统计] 成功: {success_count}/{len(all_results)}，"
                  f"基础结果 {total_entities} 条，附件明细 {total_details} 条")

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

    def _load_previous_results(
        self,
        explicit_path: str | None = None,
        *,
        raw_output_filename: str = "raw_results.json",
        run_mode: RunMode = "both",
        successful_only: bool = True,
    ) -> tuple:
        """
        加载上次运行的成功结果（用于断点续跑）

        Returns:
            (成功结果列表, 已成功的公告ID集合)
        """
        writer = getattr(self, "writer", None)
        output_dir = Path(getattr(writer, "output_dir", config.OUTPUT_DIR))
        checkpoint_path = self._checkpoint_path(raw_output_filename, run_mode)
        raw_path = (
            Path(explicit_path)
            if explicit_path
            else (
                checkpoint_path
                if checkpoint_path.exists()
                else output_dir / raw_output_filename
            )
        )
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
            if successful_only and not item.get("success"):
                continue
            result = parse_extraction_result(item)
            prev_results.append(result)
            skip_ids.add(result.announcement_id)

        return prev_results, skip_ids

    def _checkpoint_path(
        self,
        raw_output_filename: str = "raw_results.json",
        run_mode: RunMode = "both",
    ) -> Path:
        """返回与输出文件及运行层隔离的检查点路径。"""
        writer = getattr(self, "writer", None)
        output_dir = Path(getattr(writer, "output_dir", config.OUTPUT_DIR))
        raw_name = Path(raw_output_filename).name
        if raw_name == "raw_results.json" and run_mode == "both":
            checkpoint_name = "raw_results.checkpoint.json"
        else:
            checkpoint_name = f"{Path(raw_name).stem}.{run_mode}.checkpoint.json"
        return output_dir / checkpoint_name

    def _write_checkpoint(
        self,
        results: List[ExtractionResult],
        *,
        raw_output_filename: str = "raw_results.json",
        run_mode: RunMode = "both",
    ):
        """原子写入中断检查点，避免进程退出时破坏已有进度。"""
        writer = getattr(self, "writer", None)
        output_dir = Path(getattr(writer, "output_dir", config.OUTPUT_DIR))
        output_dir.mkdir(parents=True, exist_ok=True)
        checkpoint_path = self._checkpoint_path(raw_output_filename, run_mode)
        (writer or ResultWriter(output_dir)).write_raw_results(
            results,
            filename=checkpoint_path.name,
        )

    def _clear_checkpoint(
        self,
        *,
        raw_output_filename: str = "raw_results.json",
        run_mode: RunMode = "both",
    ):
        """正式结果写入成功后清理临时检查点。"""
        checkpoint_path = self._checkpoint_path(raw_output_filename, run_mode)
        checkpoint_path.unlink(missing_ok=True)
