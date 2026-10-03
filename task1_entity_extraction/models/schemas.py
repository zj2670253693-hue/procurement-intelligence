"""
任务一：数据模型定义
定义公告解析结果、提取结果等核心数据结构
"""
import re
import unicodedata
from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field


class AnnouncementContent(BaseModel):
    """单篇公告的原始内容（解析后）"""
    announcement_id: str = Field(..., description="公告唯一标识（文件名）")
    title: str = Field("", description="公告标题")
    html_text: str = Field("", description="从 HTML 中提取的纯文本")
    html_tables: List[List[List[str]]] = Field(default_factory=list, description="HTML 中的表格数据")
    attachment_files: List[str] = Field(default_factory=list, description="附件文件名列表")
    attachment_texts: Dict[str, str] = Field(default_factory=dict, description="附件文本内容 {文件名: 文本}")
    attachment_tables: Dict[str, List[List[List[str]]]] = Field(default_factory=dict, description="附件中的表格")

    @property
    def full_text(self) -> str:
        """合并所有文本内容"""
        parts = [self.html_text]
        for fname, text in self.attachment_texts.items():
            parts.append(f"\n--- 附件：{fname} ---\n{text}")
        return "\n".join(parts)


class ExtractedEntity(BaseModel):
    """单个标的物的提取结果"""
    product_name: str = Field("", description="产品服务名称")
    category: str = Field("", description="品目")
    brand: str = Field("", description="品牌（产品供应商）")
    spec_model: str = Field("", description="规格型号")
    unit_price: str = Field("", description="单价")
    quantity: str = Field("", description="数量")
    total_price: str = Field("", description="总价")

    def to_dict(self) -> Dict[str, str]:
        return {
            "产品服务名称": self.product_name,
            "品目": self.category,
            "品牌（产品供应商）": self.brand,
            "规格型号": self.spec_model,
            "单价": self.unit_price,
            "数量": self.quantity,
            "总价": self.total_price,
        }


ENTITY_FIELDS = (
    "product_name", "category", "brand", "spec_model",
    "unit_price", "quantity", "total_price",
)


def deduplicate_entities(entities: List[ExtractedEntity]) -> List[ExtractedEntity]:
    """按七字段归一化后的完整内容稳定去重，保留首次出现顺序。"""
    unique = []
    seen = set()
    for entity in entities:
        key = tuple(
            re.sub(r"\s+", "", unicodedata.normalize("NFKC", getattr(entity, field) or ""))
            for field in ENTITY_FIELDS
        )
        if key in seen:
            continue
        seen.add(key)
        unique.append(entity)
    return unique


class ExtractionResult(BaseModel):
    """单篇公告的完整提取结果"""
    announcement_id: str = Field(..., description="公告ID")
    entities: List[ExtractedEntity] = Field(default_factory=list, description="提取到的标的物列表")
    extra_info: Dict[str, Any] = Field(default_factory=dict, description="额外信息（任务二需要的字段等）")
    raw_response: str = Field("", description="LLM 原始返回（用于调试）")
    success: bool = Field(True, description="是否提取成功")
    error_message: str = Field("", description="错误信息（如果失败）")

    def to_records(self) -> List[Dict[str, str]]:
        """转换为扁平记录列表（用于输出 CSV/Excel）"""
        records = []
        for entity in self.entities:
            record = {"公告ID": self.announcement_id}
            record.update(entity.to_dict())
            records.append(record)
        return records


class EvaluationMetrics(BaseModel):
    """评测指标"""
    accuracy: float = Field(0.0, description="准确率")
    precision: float = Field(0.0, description="精确率")
    recall: float = Field(0.0, description="召回率")
    f1_score: float = Field(0.0, description="F1 分数")
    field_stats: Dict[str, Dict[str, float]] = Field(default_factory=dict, description="各字段统计")
    total_samples: int = Field(0, description="样本总数")
    total_fields: int = Field(0, description="字段总数")
    correct_fields: int = Field(0, description="正确字段数")
