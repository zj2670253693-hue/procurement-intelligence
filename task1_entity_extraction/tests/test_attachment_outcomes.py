import zipfile

from data_loader.attachment_parser import (
    AttachmentParser,
    classify_attachment_status,
)


def test_missing_zip_referenced_by_html_is_missing():
    assert classify_attachment_status("规格型号详见附件", False, None) == "missing"


def test_absent_zip_without_reference_is_no_attachment():
    assert classify_attachment_status("本项目无附件", False, None) == "no_attachment"


def test_mixed_zip_keeps_readable_file_and_reports_failed_file(tmp_path):
    zip_path = tmp_path / "fictional-001.zip"
    with zipfile.ZipFile(zip_path, "w") as archive:
        archive.writestr("可读.txt", "设备甲 | 型号X-1")
        archive.writestr("损坏.pdf", b"not a pdf")

    outcome = AttachmentParser().parse_zip_with_status(str(zip_path))

    assert outcome.status == "available"
    assert outcome.archive_files == ["可读.txt", "损坏.pdf"]
    assert outcome.parsed_files == ["可读.txt"]
    assert outcome.failed_files == ["损坏.pdf"]
    assert outcome.texts["可读.txt"] == "设备甲 | 型号X-1"


def test_corrupt_zip_is_unreadable(tmp_path):
    zip_path = tmp_path / "fictional-002.zip"
    zip_path.write_bytes(b"this is not a zip archive")

    outcome = AttachmentParser().parse_zip_with_status(str(zip_path))

    assert outcome.status == "unreadable"
    assert outcome.texts == {}
    assert outcome.tables == {}
    assert "ZIP" in outcome.error_message


def test_existing_zip_with_no_supported_files_is_unreadable(tmp_path):
    zip_path = tmp_path / "fictional-003.zip"
    with zipfile.ZipFile(zip_path, "w") as archive:
        archive.writestr("说明.bin", b"binary")

    outcome = AttachmentParser().parse_zip_with_status(str(zip_path))

    assert outcome.status == "unreadable"
    assert outcome.archive_files == ["说明.bin"]
    assert outcome.parsed_files == []


def test_legacy_parse_zip_still_returns_text_and_table_tuple(tmp_path):
    zip_path = tmp_path / "fictional-004.zip"
    with zipfile.ZipFile(zip_path, "w") as archive:
        archive.writestr("说明.txt", "虚构附件内容")

    texts, tables = AttachmentParser().parse_zip(str(zip_path))

    assert texts == {"说明.txt": "虚构附件内容"}
    assert tables == {}
