"""
LLM 实体提取器
使用 Qwen / DeepSeek API 进行实体提取，支持重试和错误处理
"""
import json
import time
import re
from typing import Optional, Dict, Any

import httpx
from openai import OpenAI

import config
from models.schemas import ExtractedEntity, ExtractionResult, deduplicate_entities
from extractor.prompts import build_extraction_prompt, build_extra_info_prompt


class LLMExtractor:
    """基于大模型 API 的实体提取器"""

    def __init__(self, provider: str = None, api_key: str = None, base_url: str = None, model: str = None):
        self.provider = (provider or config.LLM_PROVIDER).lower()

        if self.provider == "qwen":
            self.api_key = api_key or config.QWEN_API_KEY
            self.base_url = base_url or config.QWEN_BASE_URL
            self.model = model or config.QWEN_MODEL
        elif self.provider == "deepseek":
            self.api_key = api_key or config.DEEPSEEK_API_KEY
            self.base_url = base_url or config.DEEPSEEK_BASE_URL
            self.model = model or config.DEEPSEEK_MODEL
        else:
            raise ValueError(f"不支持的 LLM provider: {self.provider}，请使用 'qwen' 或 'deepseek'")

        if not self.api_key:
            raise ValueError(
                f"未设置 {self.provider.upper()}_API_KEY。"
                f"请在环境变量或 .env 文件中配置，或在初始化时传入 api_key 参数。"
            )

        self.client = OpenAI(
            api_key=self.api_key,
            base_url=self.base_url,
            timeout=config.LLM_TIMEOUT,
        )

    def extract(self, text: str, has_tables: bool = False, announcement_id: str = "") -> ExtractionResult:
        """
        从文本中提取 7 个核心字段
        """
        result = ExtractionResult(announcement_id=announcement_id)

        try:
            system_prompt, user_prompt = build_extraction_prompt(text, has_tables)
            response_text = self._call_llm(system_prompt, user_prompt)
            result.raw_response = response_text

            # 解析 JSON
            entities = self._deduplicate_entities(self._parse_entities(response_text))
            result.entities = entities

            if not entities:
                result.success = False
                result.error_message = "未能从 LLM 响应中解析出任何实体"

        except Exception as e:
            result.success = False
            result.error_message = str(e)

        return result

    def extract_extra_info(self, text: str, announcement_id: str = "") -> Dict[str, Any]:
        """提取任务二需要的额外信息（项目名称、采购单位、中标供应商等）"""
        try:
            system_prompt, user_prompt = build_extra_info_prompt(text)
            response_text = self._call_llm(system_prompt, user_prompt)
            return self._parse_json_response(response_text)
        except Exception as e:
            return {"error": str(e)}

    def _call_llm(self, system_prompt: str, user_prompt: str) -> str:
        """调用 LLM API，带重试机制"""
        last_error = None

        for attempt in range(1, config.LLM_MAX_RETRIES + 1):
            try:
                response = self.client.chat.completions.create(
                    model=self.model,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                    temperature=config.LLM_TEMPERATURE,
                    max_tokens=config.LLM_MAX_TOKENS,
                )
                return response.choices[0].message.content

            except Exception as e:
                last_error = e
                if attempt < config.LLM_MAX_RETRIES:
                    wait_time = config.LLM_RETRY_DELAY * attempt
                    print(f"  [重试 {attempt}/{config.LLM_MAX_RETRIES}] LLM 调用失败: {e}，等待 {wait_time}s...")
                    time.sleep(wait_time)

        raise Exception(f"LLM 调用失败（已重试 {config.LLM_MAX_RETRIES} 次）: {last_error}")

    def _parse_entities(self, response_text: str) -> list:
        """从 LLM 响应中解析实体列表"""
        data = self._parse_json_response(response_text)
        if not data:
            return []

        entities = []
        raw_entities = data.get("entities", [])

        for item in raw_entities:
            if not isinstance(item, dict):
                continue
            entity = ExtractedEntity(
                product_name=str(item.get("product_name", "")).strip(),
                category=str(item.get("category", "")).strip(),
                brand=str(item.get("brand", "")).strip(),
                spec_model=str(item.get("spec_model", "")).strip(),
                unit_price=str(item.get("unit_price", "")).strip(),
                quantity=str(item.get("quantity", "")).strip(),
                total_price=str(item.get("total_price", "")).strip(),
            )
            # 至少有一个字段非空才保留
            if any([entity.product_name, entity.category, entity.brand,
                    entity.spec_model, entity.unit_price, entity.quantity, entity.total_price]):
                entities.append(entity)

        return entities

    @staticmethod
    def _deduplicate_entities(entities: list[ExtractedEntity]) -> list[ExtractedEntity]:
        """
        删除模型因“HTML 正文 + 附件重复出现同一清单”产生的完全重复行。

        这里只合并七个字段归一化后完全一致的记录，避免把同名但属于不同
        包件、数量或价格不同的真实标的物误合并。
        """
        return deduplicate_entities(entities)

    def _parse_json_response(self, response_text: str) -> Optional[Dict]:
        """从 LLM 响应文本中解析 JSON（处理 markdown 代码块等情况）"""
        if not response_text:
            return None

        # 尝试直接解析
        try:
            return json.loads(response_text)
        except json.JSONDecodeError:
            pass

        # 尝试从 markdown 代码块中提取
        code_block_pattern = r"```(?:json)?\s*\n?(.*?)```"
        matches = re.findall(code_block_pattern, response_text, re.DOTALL)
        for match in matches:
            try:
                return json.loads(match.strip())
            except json.JSONDecodeError:
                continue

        # 尝试找到第一个 { 到最后一个 }
        start = response_text.find("{")
        end = response_text.rfind("}")
        if start != -1 and end != -1 and end > start:
            try:
                return json.loads(response_text[start:end + 1])
            except json.JSONDecodeError:
                pass

        # 尝试找到第一个 [ 到最后一个 ]
        start = response_text.find("[")
        end = response_text.rfind("]")
        if start != -1 and end != -1 and end > start:
            try:
                data = json.loads(response_text[start:end + 1])
                return {"entities": data}
            except json.JSONDecodeError:
                pass

        # 最后尝试：从被截断的 JSON 中抢救出完整的实体对象
        salvaged = self._salvage_entities(response_text)
        if salvaged:
            print(f"  [提示] LLM 响应被截断，已抢救出 {len(salvaged['entities'])} 个实体")
            return salvaged

        print(f"  [警告] 无法解析 LLM 响应为 JSON: {response_text[:200]}...")
        return None

    def _salvage_entities(self, response_text: str) -> Optional[Dict]:
        """
        当 LLM 响应因 max_tokens 被截断、JSON 未闭合时，
        用括号配对扫描出所有完整的实体对象，抢救可用数据
        """
        idx = response_text.find('"entities"')
        arr_start = response_text.find("[", idx if idx != -1 else 0)
        if arr_start == -1:
            return None

        objects = []
        depth = 0
        obj_start = None
        in_str = False
        escaped = False

        for i in range(arr_start + 1, len(response_text)):
            ch = response_text[i]
            if in_str:
                if escaped:
                    escaped = False
                elif ch == "\\":
                    escaped = True
                elif ch == '"':
                    in_str = False
                continue
            if ch == '"':
                in_str = True
            elif ch == "{":
                if depth == 0:
                    obj_start = i
                depth += 1
            elif ch == "}":
                if depth > 0:
                    depth -= 1
                    if depth == 0 and obj_start is not None:
                        try:
                            objects.append(json.loads(response_text[obj_start:i + 1]))
                        except json.JSONDecodeError:
                            pass
                        obj_start = None
            elif ch == "]" and depth == 0:
                break

        return {"entities": objects} if objects else None
