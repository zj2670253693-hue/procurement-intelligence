"""
评测模块
计算提取结果的准确率、精确率、召回率
赛题评分公式：提取准确性 = 准确率×0.4 + 精确率×0.3 + 召回率×0.3
"""
from decimal import Decimal, InvalidOperation
import re
import unicodedata
from typing import List, Dict, Tuple
from models.schemas import ExtractionResult, EvaluationMetrics, ExtractedEntity
import config


class Evaluator:
    """提取结果评测器"""

    # 字段映射：用于对比
    FIELD_KEYS = ["product_name", "category", "brand", "spec_model", "unit_price", "quantity", "total_price"]

    def evaluate(
        self,
        predictions: List[ExtractionResult],
        ground_truths: List[Dict],
    ) -> EvaluationMetrics:
        """
        评测提取结果

        predictions: 模型提取结果列表
        ground_truths: 人工标注的标准答案列表，格式：
            [
                {"announcement_id": "xxx", "entities": [{"product_name": "...", ...}]},
                ...
            ]

        指标口径（按字段统计）：
            TP = 预测值非空 且 标准答案非空 且 两者匹配
            FP = 预测值非空 但 未匹配上（值错，或标准答案该处为空）
            FN = 标准答案非空 但 未被正确预测
            精确率 = TP / (TP + FP)
            召回率 = TP / (TP + FN)
            准确率 = TP / (TP + FP + FN)
        """
        gt_map = {gt["announcement_id"]: gt for gt in ground_truths}
        pred_map = {}
        for pred in predictions:
            if pred.announcement_id not in gt_map:
                continue
            if pred.announcement_id in pred_map:
                raise ValueError(f"预测结果中公告 ID 重复: {pred.announcement_id}")
            pred_map[pred.announcement_id] = pred

        tp = 0
        fp = 0
        fn = 0
        field_stats = {f: {"tp": 0, "fp": 0, "fn": 0} for f in self.FIELD_KEYS}
        for aid, ground_truth in gt_map.items():
            gt_entities = ground_truth.get("entities", [])
            pred = pred_map.get(aid)
            pred_entities = [e.model_dump() for e in pred.entities] if pred else []

            # 先做实体对齐，避免因输出顺序不同导致全盘错配
            pairs, unmatched_pred, unmatched_gt = self._align(pred_entities, gt_entities)

            # 对齐上的实体：逐字段比对
            for pi, gi in pairs:
                for field in self.FIELD_KEYS:
                    pred_val = str(pred_entities[pi].get(field, "")).strip()
                    gt_val = str(gt_entities[gi].get(field, "")).strip()
                    if not pred_val and not gt_val:
                        continue
                    if pred_val and gt_val and self._field_match(pred_val, gt_val, field):
                        tp += 1
                        field_stats[field]["tp"] += 1
                    else:
                        # 值错、或一方为空
                        if pred_val:
                            fp += 1
                            field_stats[field]["fp"] += 1
                        if gt_val:
                            fn += 1
                            field_stats[field]["fn"] += 1

            # 多预测出来的实体：其非空字段全部计入误报
            for pi in unmatched_pred:
                for field in self.FIELD_KEYS:
                    if str(pred_entities[pi].get(field, "")).strip():
                        fp += 1
                        field_stats[field]["fp"] += 1

            # 漏掉的实体：其非空字段全部计入漏报
            for gi in unmatched_gt:
                for field in self.FIELD_KEYS:
                    if str(gt_entities[gi].get(field, "")).strip():
                        fn += 1
                        field_stats[field]["fn"] += 1

        precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
        recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
        accuracy = tp / (tp + fp + fn) if (tp + fp + fn) > 0 else 0.0
        f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0.0

        field_stats_out = {}
        for f, s in field_stats.items():
            p = s["tp"] / (s["tp"] + s["fp"]) if (s["tp"] + s["fp"]) > 0 else 0.0
            r = s["tp"] / (s["tp"] + s["fn"]) if (s["tp"] + s["fn"]) > 0 else 0.0
            field_stats_out[f] = {
                "precision": round(p, 4),
                "recall": round(r, 4),
                "correct": s["tp"],
                "predicted": s["tp"] + s["fp"],
                "relevant": s["tp"] + s["fn"],
            }

        return EvaluationMetrics(
            accuracy=round(accuracy, 4),
            precision=round(precision, 4),
            recall=round(recall, 4),
            f1_score=round(f1, 4),
            field_stats=field_stats_out,
            total_samples=len(gt_map),
            total_fields=tp + fp + fn,
            correct_fields=tp,
        )

    def _align(
        self,
        pred_entities: List[Dict],
        gt_entities: List[Dict],
    ) -> Tuple[List[Tuple[int, int]], List[int], List[int]]:
        """
        实体对齐：把预测实体与标准答案实体配对

        采用贪心最佳匹配——先计算所有 (预测, 标准) 组合的相似度，
        再按相似度从高到低两两配对，避免"按顺序硬比"导致的错配。

        Returns:
            (配对列表 [(预测下标, 标准下标)], 未配对的预测下标, 未配对的标准下标)
        """
        if not pred_entities or not gt_entities:
            return [], list(range(len(pred_entities))), list(range(len(gt_entities)))

        # 计算相似度矩阵
        scored = []
        for pi, pe in enumerate(pred_entities):
            for gi, ge in enumerate(gt_entities):
                gt_non_empty = [f for f in self.FIELD_KEYS if str(ge.get(f, "")).strip()]
                if not gt_non_empty:
                    continue
                hit = 0
                for f in gt_non_empty:
                    pv = str(pe.get(f, "")).strip()
                    gv = str(ge.get(f, "")).strip()
                    if pv and self._field_match(pv, gv, f):
                        hit += 1
                if hit > 0:
                    scored.append((hit / len(gt_non_empty), hit, pi, gi))

        # 相似度高的优先配对；同分时命中字段数多的优先
        scored.sort(key=lambda x: (-x[0], -x[1]))

        used_pred, used_gt = set(), set()
        pairs = []
        for _, _, pi, gi in scored:
            if pi in used_pred or gi in used_gt:
                continue
            pairs.append((pi, gi))
            used_pred.add(pi)
            used_gt.add(gi)

        unmatched_pred = [i for i in range(len(pred_entities)) if i not in used_pred]
        unmatched_gt = [i for i in range(len(gt_entities)) if i not in used_gt]
        return pairs, unmatched_pred, unmatched_gt

    def _field_match(self, pred: str, gt: str, field: str = "") -> bool:
        """按字段类型判断两个值是否匹配，避免“数字碰巧相同”的误判。"""
        norm_pred = self._normalize_text(pred)
        norm_gt = self._normalize_text(gt)

        if norm_pred == norm_gt:
            return True

        # 只有金额字段才按金额比较。旧实现会把“服务器1”和“路由器1”
        # 因为都含数字 1 而误判为相同。
        if field in {"unit_price", "total_price"}:
            amount_pred = self._normalize_amount(pred)
            amount_gt = self._normalize_amount(gt)
            return amount_pred is not None and amount_gt is not None and amount_pred == amount_gt

        # 名称、品目、品牌和规格允许较强的包含关系，但短片段或差异过大
        # 不算命中，避免用一个通用词匹配整段描述。
        if field in {"product_name", "category", "brand", "spec_model"}:
            shorter, longer = sorted((norm_pred, norm_gt), key=len)
            if len(shorter) >= 4 and len(shorter) / len(longer) >= 0.6 and shorter in longer:
                return True

        return False

    @staticmethod
    def _normalize_text(text: str) -> str:
        """统一全半角、大小写、空白和常见分隔符。"""
        text = unicodedata.normalize("NFKC", str(text or "")).lower()
        return re.sub(r"[^0-9a-z_\u4e00-\u9fff]+", "", text)

    @staticmethod
    def _normalize_amount(text: str) -> Decimal | None:
        """把元、万元、亿元统一换算成“元”后比较。"""
        normalized = unicodedata.normalize("NFKC", str(text or "")).replace(",", "")
        match = re.search(r"[-+]?\d+(?:\.\d+)?", normalized)
        if not match:
            return None
        try:
            value = Decimal(match.group(0))
        except InvalidOperation:
            return None

        if "亿元" in normalized:
            value *= Decimal("100000000")
        elif "万元" in normalized or re.search(r"\d万(?:\D|$)", normalized):
            value *= Decimal("10000")
        return value.normalize()

    def print_report(self, metrics: EvaluationMetrics):
        """打印评测报告"""
        print("\n" + "=" * 60)
        print("  评测报告")
        print("=" * 60)
        print(f"  样本数: {metrics.total_samples}")
        print(f"  总字段数: {metrics.total_fields}")
        print(f"  正确字段数: {metrics.correct_fields}")
        print("-" * 60)
        print(f"  准确率 (Accuracy):  {metrics.accuracy:.2%}")
        print(f"  精确率 (Precision): {metrics.precision:.2%}")
        print(f"  召回率 (Recall):    {metrics.recall:.2%}")
        print(f"  F1 分数:            {metrics.f1_score:.2%}")
        print("-" * 60)
        composite = metrics.accuracy * 0.4 + metrics.precision * 0.3 + metrics.recall * 0.3
        print(f"  赛题综合得分:       {composite:.2%} （准确率×0.4 + 精确率×0.3 + 召回率×0.3）")
        print("-" * 60)
        print("  各字段详情:")
        for field, stats in metrics.field_stats.items():
            label = config.FIELD_LABELS.get(field, field)
            print(f"    {label:20s} P={stats['precision']:.2%} R={stats['recall']:.2%} "
                  f"(正确{stats['correct']}/预测{stats['predicted']}/标准{stats['relevant']})")
        print("=" * 60)
