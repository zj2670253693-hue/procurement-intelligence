"""
结果输出模块
将提取结果写入 CSV / Excel / JSON 文件
"""
import json
import os
from pathlib import Path
from typing import List

import pandas as pd

from models.schemas import ExtractionResult
from models.result_io import serialize_extraction_result
import config


class ResultWriter:
    """提取结果写入器"""

    def __init__(self, output_dir: str = None):
        self.output_dir = Path(output_dir) if output_dir else config.OUTPUT_DIR
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def write(self, results: List[ExtractionResult], filename: str = None, fmt: str = None) -> str:
        """
        将提取结果写入文件
        返回输出文件路径
        """
        fmt = (fmt or config.OUTPUT_FORMAT).lower()
        filename = filename or config.OUTPUT_FILENAME

        # 转换为扁平记录
        all_records = []
        for result in results:
            records = result.to_records()
            for record in records:
                # 添加额外信息
                if result.extra_info:
                    for k, v in result.extra_info.items():
                        if isinstance(v, list):
                            record[k] = "; ".join(str(x) for x in v)
                        else:
                            record[k] = str(v) if v else ""
            all_records.extend(records)

        if not all_records:
            print("[警告] 没有可输出的记录")
            return ""

        df = pd.DataFrame(all_records)

        if fmt == "csv":
            output_path = self.output_dir / f"{filename}.csv"
            df.to_csv(output_path, index=False, encoding="utf-8-sig")
        elif fmt in ("excel", "xlsx"):
            output_path = self.output_dir / f"{filename}.xlsx"
            df.to_excel(output_path, index=False, engine="openpyxl")
        elif fmt == "json":
            output_path = self.output_dir / f"{filename}.json"
            with open(output_path, "w", encoding="utf-8") as f:
                json.dump(all_records, f, ensure_ascii=False, indent=2)
        else:
            raise ValueError(f"不支持的输出格式: {fmt}")

        print(f"[输出] 结果已写入: {output_path}（共 {len(all_records)} 条记录）")
        return str(output_path)

    def _resolve_output_path(self, filename: str) -> Path:
        """Resolve a raw-result name without nesting a given directory twice.

        ``filename`` may be a bare name (placed in ``output_dir``), may repeat
        the output directory name (``output/raw.json``), or may be absolute.
        Directory parts are kept relative to ``output_dir`` so a caller can
        never end up writing ``output/output/raw.json``.
        """
        candidate = Path(filename)
        if candidate.is_absolute():
            output_path = candidate
        elif candidate.parent == Path("."):
            output_path = self.output_dir / candidate
        else:
            parts = list(candidate.parts)
            while len(parts) > 1 and parts[0] == self.output_dir.name:
                parts.pop(0)
            output_path = self.output_dir.joinpath(*parts)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        return output_path

    def write_raw_results(self, results: List[ExtractionResult], filename: str = "raw_results.json"):
        """原子写入双层原始结果；不落盘模型原始响应。"""
        output_path = self._resolve_output_path(filename)
        data = [serialize_extraction_result(result) for result in results]
        temp_path = output_path.with_name(f".{output_path.name}.tmp")
        with open(temp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(temp_path, output_path)
        print(f"[输出] 原始结果已写入: {output_path}")
