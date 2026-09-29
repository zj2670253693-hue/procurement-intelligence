"""
结果输出模块
将提取结果写入 CSV / Excel / JSON 文件
"""
import json
from pathlib import Path
from typing import List

import pandas as pd

from models.schemas import ExtractionResult
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

    def write_raw_results(self, results: List[ExtractionResult], filename: str = "raw_results.json"):
        """写入原始结果（含 LLM 原始响应，用于调试）"""
        output_path = self.output_dir / filename
        data = []
        for r in results:
            data.append({
                "announcement_id": r.announcement_id,
                "success": r.success,
                "error": r.error_message,
                "entities": [e.model_dump() for e in r.entities],
                "extra_info": r.extra_info,
            })
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        print(f"[输出] 原始结果已写入: {output_path}")
