"""Bounded, non-executing XLSX validation before openpyxl sees an upload.

Formula cells are deliberately rejected: cached values may be absent or stale.
An administrator must export a values-only workbook, retaining the original file
in staging for audit. This module never evaluates or rewrites Excel formulas.
"""
from __future__ import annotations

import io
import re
import stat
import zipfile
from xml.etree.ElementTree import ParseError

from defusedxml.ElementTree import iterparse
from defusedxml.common import DefusedXmlException


class ValidationError(ValueError):
    """Safe validation error suitable for a report (no cell content in message)."""

    def __init__(self, message: str, *, code: str = "invalid_workbook", report=None):
        super().__init__(message)
        self.code = code
        self.errors = [message]
        self.report = report or {"valid": False, "row_count": 0, "errors": self.errors, "code": code}


def _setting(settings, name, default):
    return getattr(settings, name, default) if settings is not None else default


def _reject(message, code):
    raise ValidationError(message, code=code)


def _column_index(cell_reference: str) -> int:
    match = re.fullmatch(r"([A-Z]+)([1-9][0-9]*)", cell_reference)
    if match is None:
        _reject("Excel có tham chiếu ô không hợp lệ.", "invalid_cell_reference")
    result = 0
    for char in match.group(1):
        result = result * 26 + ord(char) - ord("A") + 1
    return result


def validate_workbook(content: bytes, filename: str, settings=None, classification: bool = False) -> None:
    """Validate package structure/security/dimensions without loading cells in RAM.

    Semantic/schema checks occur in transform_inventory / parse_classification,
    which reuse the application's canonical rules after this security boundary.
    The checks cover every worksheet, including hidden or unselected sheets.
    """
    if not isinstance(content, bytes) or not content:
        _reject("File Excel trống hoặc không phải nội dung nhị phân.", "empty_file")
    if not filename or not filename.casefold().endswith(".xlsx"):
        _reject("Chỉ chấp nhận file .xlsx, không nhận .xls hoặc .xlsm.", "invalid_extension")
    if len(content) > _setting(settings, "max_upload_mb", 25) * 1024 * 1024:
        _reject("File vượt giới hạn dung lượng tải lên đã cấu hình.", "upload_limit")
    if not content.startswith(b"PK\x03\x04") or not zipfile.is_zipfile(io.BytesIO(content)):
        _reject("File không phải gói Excel XLSX hợp lệ.", "invalid_zip")
    max_bytes = _setting(settings, "max_uncompressed_mb", 512) * 1024 * 1024
    max_rows = _setting(settings, "max_rows", 250000)
    max_columns = _setting(settings, "max_columns", 200)
    # ZIP central-directory bounds are checked before decompressing any XML.
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            members = archive.infolist()
            if len(members) > 4096:
                _reject("Excel chứa quá nhiều thành phần ZIP.", "member_limit")
            names = set()
            total_bytes = 0
            for member in members:
                name = member.filename
                parts = (name[:-1] if name.endswith("/") else name).split("/")
                if ("\\" in name or name.startswith("/") or ".." in parts
                        or "." in parts or "" in parts or ":" in name or "\x00" in name or name in names
                        or stat.S_ISLNK(member.external_attr >> 16)):
                    _reject("Gói Excel có đường dẫn thành phần không an toàn hoặc trùng lặp.", "unsafe_zip_path")
                names.add(name)
                if member.flag_bits & 1:
                    _reject("Không nhận Excel/ZIP được mã hóa hoặc bảo vệ bằng mật khẩu.", "encrypted_zip")
                total_bytes += member.file_size
                if total_bytes > max_bytes or member.file_size > max_bytes:
                    _reject("Dung lượng giải nén Excel vượt giới hạn an toàn.", "uncompressed_limit")
                lower = name.casefold()
                if ("vbaproject" in lower or lower.startswith("xl/externallinks/")
                        or lower.startswith("xl/embeddings/") or lower.startswith("xl/macrosheets/")
                        or lower.startswith("xl/querytables/") or lower == "xl/connections.xml"
                        or lower.endswith(".bin")):
                    _reject("Không nhận Excel có macro, liên kết ngoài hoặc đối tượng nhúng.", "active_content")
            if not {"[Content_Types].xml", "_rels/.rels", "xl/workbook.xml"}.issubset(names):
                _reject("Thiếu thành phần bắt buộc của định dạng XLSX.", "invalid_xlsx_package")
            worksheet_count = 0
            for member in members:
                lower = member.filename.casefold()
                if not (lower.endswith(".xml") or lower.endswith(".rels")):
                    continue
                is_sheet = lower.startswith("xl/worksheets/") and lower.endswith(".xml")
                worksheet_count += int(is_sheet)
                if worksheet_count > 128:
                    _reject("Excel có quá nhiều worksheet.", "worksheet_limit")
                row_count = 0
                with archive.open(member) as stream:
                    # defusedxml rejects declarations/entities before expansion,
                    # including UTF-16 XML and declarations across buffer boundaries.
                    events = iterparse(stream, events=("end",), forbid_dtd=True,
                                       forbid_entities=True, forbid_external=True)
                    for _, element in events:
                        tag = element.tag.rsplit("}", 1)[-1]
                        lowered_tag = tag.casefold()
                        if lowered_tag in {"f", "formula", "formula1", "formula2", "definedname"}:
                            _reject("Excel có công thức/defined name. Hãy xuất bản chỉ có giá trị (values-only) rồi thử lại; không dùng giá trị cache của công thức.", "formula_not_allowed")
                        if lowered_tag in {"externalreference", "externallink", "ddelink", "ddelinks", "olelink", "oleobject"}:
                            _reject("Không nhận liên kết ngoài, DDE hoặc đối tượng OLE trong Excel.", "external_content")
                        if lowered_tag == "relationship" and (element.attrib.get("TargetMode", "").strip().casefold() == "external"
                                or re.match(r"^[A-Za-z][A-Za-z0-9+.-]*:", element.attrib.get("Target", ""))
                                or element.attrib.get("Target", "").startswith("//")):
                            _reject("Excel có liên kết tới nguồn bên ngoài (kể cả hyperlink).", "external_relationship")
                        if lowered_tag in {"override", "default"}:
                            content_type = element.attrib.get("ContentType", "").casefold()
                            if "macroenabled" in content_type or "macrosheet" in content_type or "vba" in content_type:
                                _reject("Không nhận định dạng Excel có macro.", "active_content")
                        if is_sheet:
                            if tag == "dimension":
                                reference = element.attrib.get("ref", "A1")
                                edges = reference.split(":")
                                if len(edges) > 2:
                                    _reject("Excel có kích thước worksheet không hợp lệ.", "invalid_dimension")
                                for edge in edges:
                                    column = _column_index(edge)
                                    row = int(re.search(r"[0-9]+$", edge).group())
                                    if column > max_columns or row > max_rows + 30:
                                        _reject("Kích thước worksheet vượt giới hạn dòng/cột đã cấu hình.", "dimension_limit")
                            elif tag == "row":
                                row_count += 1
                                if row_count > max_rows + 30:
                                    _reject("Excel vượt giới hạn số dòng đã cấu hình.", "row_limit")
                                try:
                                    row_index = int(element.attrib.get("r", row_count))
                                except ValueError:
                                    _reject("Excel có số dòng không hợp lệ.", "invalid_row_reference")
                                if row_index > max_rows + 30 or row_index < 1:
                                    _reject("Excel vượt giới hạn số dòng đã cấu hình.", "row_limit")
                            elif tag == "c":
                                reference = element.attrib.get("r", "")
                                column = _column_index(reference)
                                row = int(re.search(r"[0-9]+$", reference).group())
                                if column > max_columns or row > max_rows + 30:
                                    _reject("Excel vượt giới hạn dòng/cột đã cấu hình.", "dimension_limit")
                        element.clear()
            if not worksheet_count:
                _reject("Excel không có worksheet dữ liệu.", "missing_worksheet")
    except ValidationError:
        raise
    except (DefusedXmlException, ParseError, zipfile.BadZipFile, RuntimeError, EOFError, ValueError, OSError):
        _reject("Gói Excel hoặc XML bị hỏng/không an toàn; không thể xử lý.", "invalid_xml_or_archive")
