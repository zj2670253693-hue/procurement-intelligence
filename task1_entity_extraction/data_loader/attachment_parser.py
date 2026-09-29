"""
附件解析器
解析 zip 压缩包中的 doc/docx/xlsx/pdf 等附件文件，提取文本和表格
"""
import io
import zipfile
from pathlib import Path
from typing import Dict, List, Tuple

from models.schemas import AnnouncementContent


class AttachmentParser:
    """解析公告附件（zip 包中的多格式文件）"""

    SUPPORTED_EXTENSIONS = {".doc", ".docx", ".xlsx", ".xls", ".pdf", ".pptx", ".txt"}

    def __init__(self):
        pass

    def parse_zip(self, zip_path: str, announcement_id: str = "") -> Tuple[Dict[str, str], Dict[str, List]]:
        """
        解析 zip 附件包
        返回: (附件文本字典, 附件表格字典)
        """
        texts = {}
        tables = {}

        path = Path(zip_path)
        if not path.exists():
            return texts, tables

        try:
            with zipfile.ZipFile(zip_path, "r") as zf:
                for file_info in zf.infolist():
                    if file_info.is_dir():
                        continue

                    ext = Path(file_info.filename).suffix.lower()
                    if ext not in self.SUPPORTED_EXTENSIONS:
                        continue

                    try:
                        file_data = zf.read(file_info.filename)
                        text, table = self._parse_file(file_data, ext, file_info.filename)
                        if text:
                            texts[file_info.filename] = text
                        if table:
                            tables[file_info.filename] = table
                    except Exception:
                        # 静默跳过无法解析的附件（加密/格式不符等），HTML 中通常已有主要信息
                        continue
        except zipfile.BadZipFile:
            pass

        return texts, tables

    def parse_files(self, file_paths: List[str]) -> Tuple[Dict[str, str], Dict[str, List]]:
        """直接解析多个文件（非 zip）"""
        texts = {}
        tables = {}

        for fp in file_paths:
            path = Path(fp)
            if not path.exists():
                continue
            ext = path.suffix.lower()
            if ext not in self.SUPPORTED_EXTENSIONS:
                continue

            try:
                file_data = path.read_bytes()
                text, table = self._parse_file(file_data, ext, path.name)
                if text:
                    texts[path.name] = text
                if table:
                    tables[path.name] = table
            except Exception as e:
                print(f"  [警告] 解析文件 {fp} 失败: {e}")

        return texts, tables

    def _parse_file(self, file_data: bytes, ext: str, filename: str) -> Tuple[str, List]:
        """根据扩展名解析单个文件，返回 (文本, 表格)"""
        if ext == ".docx":
            return self._parse_docx(file_data)
        elif ext == ".doc":
            return self._parse_doc(file_data)
        elif ext in (".xlsx", ".xls"):
            return self._parse_excel(file_data)
        elif ext == ".pdf":
            return self._parse_pdf(file_data)
        elif ext == ".pptx":
            return self._parse_pptx(file_data)
        elif ext == ".txt":
            return self._parse_txt(file_data)
        else:
            return "", []

    def _parse_docx(self, file_data: bytes) -> Tuple[str, List]:
        """解析 Word .docx 文件"""
        from docx import Document

        doc = Document(io.BytesIO(file_data))

        # 提取段落文本
        text_parts = []
        for para in doc.paragraphs:
            if para.text.strip():
                text_parts.append(para.text.strip())

        # 提取表格
        tables = []
        for table in doc.tables:
            table_data = []
            for row in table.rows:
                row_data = [cell.text.strip() for cell in row.cells]
                table_data.append(row_data)
            if table_data:
                tables.append(table_data)
                # 表格内容也加入文本
                for row in table_data:
                    text_parts.append(" | ".join(row))

        return "\n".join(text_parts), tables

    def _parse_doc(self, file_data: bytes) -> Tuple[str, List]:
        """解析旧版 Word .doc 文件（需要 antiword 或其他工具，这里做降级处理）"""
        # .doc 格式较难直接解析，尝试用文本提取
        try:
            # 尝试直接读取可能的文本内容
            text = file_data.decode("utf-8", errors="ignore")
            # 简单清理
            import re
            text = re.sub(r"[^\u4e00-\u9fff\u3000-\u303f\uff00-\uffef\w\s.,;:!?()（）【】《》、，。；：！？\-]", "", text)
            text = re.sub(r"\s+", " ", text).strip()
            return text[:5000], []  # 限制长度
        except Exception:
            return "", []

    def _parse_excel(self, file_data: bytes) -> Tuple[str, List]:
        """解析 Excel 文件"""
        import openpyxl

        wb = openpyxl.load_workbook(io.BytesIO(file_data), read_only=True, data_only=True)

        text_parts = []
        all_tables = []

        for sheet_name in wb.sheetnames:
            ws = wb[sheet_name]
            sheet_data = []

            for row in ws.iter_rows(values_only=True):
                row_data = [str(cell) if cell is not None else "" for cell in row]
                # 跳过全空行
                if any(cell.strip() for cell in row_data):
                    sheet_data.append(row_data)
                    text_parts.append(" | ".join(row_data))

            if sheet_data:
                all_tables.append(sheet_data)

        wb.close()
        return "\n".join(text_parts), all_tables

    def _parse_pdf(self, file_data: bytes) -> Tuple[str, List]:
        """解析 PDF 文件（限制页数，跳过表格提取以提升速度）"""
        MAX_PDF_PAGES = 15  # 限制最大解析页数，避免超大 PDF 拖慢整体流程
        try:
            import pdfplumber
            import logging
            logging.getLogger("pdfplumber").setLevel(logging.ERROR)

            text_parts = []
            all_tables = []

            with pdfplumber.open(io.BytesIO(file_data)) as pdf:
                total_pages = len(pdf.pages)
                pages_to_parse = min(total_pages, MAX_PDF_PAGES)
                for i, page in enumerate(pdf.pages):
                    if i >= pages_to_parse:
                        break
                    # 仅提取文本，跳过表格提取（表格提取极慢且 HTML 中通常已有主要表格）
                    page_text = page.extract_text()
                    if page_text:
                        text_parts.append(page_text)

                if total_pages > MAX_PDF_PAGES:
                    text_parts.append(f"\n[PDF 共 {total_pages} 页，仅解析前 {MAX_PDF_PAGES} 页]")

            return "\n".join(text_parts), all_tables

        except ImportError:
            try:
                from PyPDF2 import PdfReader
                reader = PdfReader(io.BytesIO(file_data))
                text_parts = []
                for i, page in enumerate(reader.pages):
                    if i >= MAX_PDF_PAGES:
                        break
                    text = page.extract_text()
                    if text:
                        text_parts.append(text)
                return "\n".join(text_parts), []
            except Exception:
                return "", []

    def _parse_pptx(self, file_data: bytes) -> Tuple[str, List]:
        """解析 PPT 文件"""
        from pptx import Presentation

        prs = Presentation(io.BytesIO(file_data))
        text_parts = []

        for slide in prs.slides:
            for shape in slide.shapes:
                if hasattr(shape, "text") and shape.text.strip():
                    text_parts.append(shape.text.strip())
                if shape.has_table:
                    table = shape.table
                    for row in table.rows:
                        row_data = [cell.text.strip() for cell in row.cells]
                        text_parts.append(" | ".join(row_data))

        return "\n".join(text_parts), []

    def _parse_txt(self, file_data: bytes) -> Tuple[str, List]:
        """解析纯文本文件"""
        for encoding in ["utf-8", "gbk", "gb2312", "latin-1"]:
            try:
                text = file_data.decode(encoding)
                return text.strip(), []
            except (UnicodeDecodeError, LookupError):
                continue
        return file_data.decode("utf-8", errors="ignore").strip(), []
