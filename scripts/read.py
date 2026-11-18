"""Локальный просмотр файлов проекта в JSON: текст, CSV, DOCX.

Без сети, моделей и извлечения архивов на диск. Для PDF, изображений
и аудио запросите экспорт в TXT, CSV или DOCX.
"""

import argparse
import csv
import io
import json
import sys
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

MAX_FILE_BYTES = 5 * 1024 * 1024
MAX_UNCOMPRESSED = 20 * 1024 * 1024
W_NS = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
TEXT_EXTENSIONS = {".txt", ".text", ".md", ".markdown"}


def _read_text(raw, path):
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ValueError(
            "Файл не в кодировке UTF-8 — пересохраните в UTF-8 "
            "или экспортируйте в TXT"
        ) from exc
    return {
        "ok": True, "file": str(path), "format": "text",
        "encoding": "utf-8", "size_bytes": len(raw),
        "line_count": len(text.splitlines()), "content": text,
    }


def _read_csv(raw, path):
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ValueError(
            "CSV не в кодировке UTF-8 — пересохраните в UTF-8"
        ) from exc
    try:
        rows = list(csv.reader(io.StringIO(text)))
    except csv.Error as exc:
        raise ValueError(f"Не удалось разобрать CSV: {exc}") from exc
    if not rows or not rows[0]:
        raise ValueError("CSV пуст — нет строки заголовков")
    header = rows[0]
    names = [name.strip() for name in header]
    if any(not name for name in names):
        raise ValueError("В заголовке CSV есть пустое имя столбца")
    if len(set(names)) != len(names):
        raise ValueError("В заголовке CSV повторяются имена столбцов")
    for index, row in enumerate(rows[1:], start=2):
        if len(row) != len(header):
            raise ValueError(
                f"Строка {index}: {len(row)} значений вместо "
                f"{len(header)} — таблица неровная"
            )
    return {
        "ok": True, "file": str(path), "format": "csv",
        "encoding": "utf-8", "columns": names,
        "row_count": len(rows) - 1, "rows": rows[1:],
        "note": "Значения возвращены как текст, формулы не вычисляются",
    }


def _docx_text(paragraph):
    parts = []
    for element in paragraph.iter():
        if element.tag == W_NS + "t":
            parts.append(element.text or "")
        elif element.tag in (W_NS + "tab",):
            parts.append("\t")
        elif element.tag in (W_NS + "br", W_NS + "cr"):
            parts.append("\n")
    return "".join(parts)


def _read_docx(raw, path):
    try:
        archive = zipfile.ZipFile(io.BytesIO(raw))
    except zipfile.BadZipFile as exc:
        raise ValueError(
            "Файл не похож на DOCX (не ZIP-архив) — "
            "экспортируйте документ в DOCX или TXT"
        ) from exc
    with archive:
        infos = archive.infolist()
        total = sum(info.file_size for info in infos)
        if total > MAX_UNCOMPRESSED:
            raise ValueError(
                f"Распакованный размер {total} байт превышает лимит "
                f"{MAX_UNCOMPRESSED} — похоже на zip-бомбу, отказ"
            )
        try:
            data = archive.read("word/document.xml")
        except KeyError as exc:
            raise ValueError(
                "В архиве нет word/document.xml — это не DOCX, "
                "экспортируйте документ в DOCX или TXT"
            ) from exc
    if b"\x00" in data:
        raise ValueError(
            "Нужен DOCX с XML в UTF-8, экспортируйте документ заново"
        )
    try:
        xml_text = data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ValueError(
            "Нужен DOCX с XML в UTF-8, экспортируйте документ заново"
        ) from exc
    lowered = xml_text.lower()
    if "<!doctype" in lowered or "<!entity" in lowered:
        raise ValueError(
            "В document.xml есть DTD/ENTITY, разбор отклонён"
        )
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        raise ValueError(f"word/document.xml не читается как XML: {exc}") from exc
    paragraphs = [
        {"index": index, "text": _docx_text(paragraph)}
        for index, paragraph in enumerate(root.iter(W_NS + "p"))
    ]
    return {
        "ok": True, "file": str(path), "format": "docx",
        "paragraph_count": len(paragraphs), "paragraphs": paragraphs,
    }


def read_file(path):
    path = Path(path)
    if path.is_dir():
        raise ValueError(f"Это папка, а не файл: {path}")
    if not path.is_file():
        raise ValueError(f"Файл не найден: {path}")
    size = path.stat().st_size
    if size > MAX_FILE_BYTES:
        raise ValueError(f"Файл больше 5 МиБ ({size} байт) — лимит чтения")
    with path.open("rb") as stream:
        raw = stream.read(MAX_FILE_BYTES + 1)
    if len(raw) > MAX_FILE_BYTES:
        raise ValueError("Файл больше 5 МиБ — лимит чтения")
    extension = path.suffix.lower()
    if extension in TEXT_EXTENSIONS:
        return _read_text(raw, path)
    if extension == ".csv":
        return _read_csv(raw, path)
    if extension == ".docx":
        return _read_docx(raw, path)
    raise ValueError(
        f"Формат «{extension or 'без расширения'}» не поддерживается. "
        "Поддерживаются TXT, MD, CSV и DOCX. Для PDF, изображений "
        "и аудио экспортируйте текст в TXT, CSV или DOCX и повторите"
    )


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Прочитать файл проекта и вывести JSON"
    )
    parser.add_argument("file", help="Путь к файлу (TXT, MD, CSV, DOCX)")
    args = parser.parse_args(argv)
    try:
        result = read_file(args.file)
    except Exception as exc:
        message = str(exc) or exc.__class__.__name__
        print(json.dumps({"ok": False, "error": message}, ensure_ascii=False))
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
