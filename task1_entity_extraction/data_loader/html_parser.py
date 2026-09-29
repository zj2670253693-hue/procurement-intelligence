"""
HTML 公告解析器
解析中国政府采购网（ccgp.gov.cn）格式的招标公告 HTML 文件
"""
import re
from pathlib import Path
from typing import List, Tuple
from bs4 import BeautifulSoup

from models.schemas import AnnouncementContent


class HTMLAnnouncementParser:
    """解析政府采购公告 HTML 文件"""

    def __init__(self):
        pass

    def parse_file(self, file_path: str) -> AnnouncementContent:
        """解析单个 HTML 文件"""
        path = Path(file_path)
        if not path.exists():
            raise FileNotFoundError(f"文件不存在: {file_path}")

        html_content = path.read_text(encoding="utf-8", errors="ignore")
        return self.parse_html(html_content, announcement_id=path.stem)

    def parse_html(self, html_content: str, announcement_id: str = "") -> AnnouncementContent:
        """解析 HTML 文本内容"""
        soup = BeautifulSoup(html_content, "lxml")

        # 提取标题
        title = self._extract_title(soup)

        # 提取纯文本
        html_text = self._extract_text(soup)

        # 提取表格
        html_tables = self._extract_tables(soup)

        return AnnouncementContent(
            announcement_id=announcement_id,
            title=title,
            html_text=html_text,
            html_tables=html_tables,
        )

    def _extract_title(self, soup: BeautifulSoup) -> str:
        """提取公告标题"""
        # 尝试多种标题位置
        selectors = [
            "h1", "h2", ".title", "#title",
            ".article-title", ".news-title",
            "title",
        ]
        for selector in selectors:
            elem = soup.select_one(selector)
            if elem and elem.get_text(strip=True):
                return elem.get_text(strip=True)

        # fallback: 取第一个 h 标签或 meta
        for tag in ["h1", "h2", "h3"]:
            elem = soup.find(tag)
            if elem and elem.get_text(strip=True):
                return elem.get_text(strip=True)

        return ""

    def _extract_text(self, soup: BeautifulSoup) -> str:
        """提取页面纯文本内容"""
        # 移除 script, style 等非内容标签
        for tag in soup(["script", "style", "meta", "link", "noscript"]):
            tag.decompose()

        # 尝试找到主要内容区域
        content_div = None
        content_selectors = [
            ".vF_detail_content", ".content", "#content",
            ".article-content", ".detail-content",
            ".TRS_Editor", ".zoom",
            "table",  # 很多政府采购网公告内容在 table 里
        ]
        for selector in content_selectors:
            content_div = soup.select_one(selector)
            if content_div:
                break

        if content_div:
            text = content_div.get_text(separator="\n", strip=True)
        else:
            text = soup.get_text(separator="\n", strip=True)

        # 清理多余空白
        text = re.sub(r"\n{3,}", "\n\n", text)
        text = re.sub(r"[ \t]+", " ", text)
        return text.strip()

    def _extract_tables(self, soup: BeautifulSoup) -> List[List[List[str]]]:
        """提取页面中的所有表格"""
        tables = []
        for table in soup.find_all("table"):
            table_data = []
            for row in table.find_all("tr"):
                row_data = []
                for cell in row.find_all(["td", "th"]):
                    cell_text = cell.get_text(strip=True)
                    row_data.append(cell_text)
                if row_data:
                    table_data.append(row_data)
            if table_data:
                tables.append(table_data)
        return tables


def parse_announcement_html(file_path: str) -> AnnouncementContent:
    """便捷函数：解析公告 HTML 文件"""
    parser = HTMLAnnouncementParser()
    return parser.parse_file(file_path)
