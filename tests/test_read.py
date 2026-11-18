import io
import json
import sys
import tempfile
import unittest
import zipfile
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import read


def run(argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        try:
            code = read.main(list(argv))
        except SystemExit as exc:
            code = exc.code
    text = out.getvalue().strip()
    return code, json.loads(text) if text else None


DOC_XML = """<?xml version="1.0" encoding="UTF-8"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
<w:body>
<w:p><w:r><w:t>Первый абзац</w:t></w:r></w:p>
<w:p><w:r><w:t>Второй</w:t><w:tab/><w:t>с табом</w:t></w:r></w:p>
<w:p><w:r><w:br/><w:t>После переноса</w:t></w:r></w:p>
<w:p/>
</w:body>
</w:document>"""


def make_docx(directory, xml=DOC_XML, extra=None):
    path = Path(directory) / "doc.docx"
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("word/document.xml", xml)
        for name, data in (extra or {}).items():
            archive.writestr(name, data)
    return path


class ReadTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_text_and_markdown(self):
        path = self.dir / "notes.md"
        path.write_text("# Заголовок\nстрока два\n", encoding="utf-8")
        code, obj = run([str(path)])
        self.assertEqual(code, 0, obj)
        self.assertEqual(obj["format"], "text")
        self.assertEqual(obj["line_count"], 2)
        self.assertIn("Заголовок", obj["content"])
        plain = self.dir / "a.txt"
        plain.write_text("просто текст", encoding="utf-8")
        code, obj = run([str(plain)])
        self.assertEqual(code, 0)
        self.assertEqual(obj["content"], "просто текст")

    def test_csv_rows_columns_and_bom(self):
        path = self.dir / "t.csv"
        path.write_text("name,price\na,10\nb,20\n", encoding="utf-8-sig")
        code, obj = run([str(path)])
        self.assertEqual(code, 0, obj)
        self.assertEqual(obj["format"], "csv")
        self.assertEqual(obj["columns"], ["name", "price"])
        self.assertEqual(obj["row_count"], 2)
        self.assertEqual(obj["rows"], [["a", "10"], ["b", "20"]])

    def test_csv_rejects_bad_shapes(self):
        cases = {
            "dup.csv": "a,a\n1,2\n",
            "empty.csv": "a,,c\n1,2,3\n",
            "ragged.csv": "a,b,c\n1,2\n",
            "blank.csv": "\n",
            "formula.csv": "expr,val\n=1+1,x\n",
        }
        for name, content in cases.items():
            path = self.dir / name
            path.write_text(content, encoding="utf-8")
            code, obj = run([str(path)])
            with self.subTest(name=name):
                if name == "formula.csv":
                    self.assertEqual(code, 0, obj)
                    self.assertEqual(obj["rows"], [["=1+1", "x"]])
                else:
                    self.assertEqual(code, 1, obj)
                    self.assertFalse(obj["ok"])

    def test_docx_paragraphs_with_index(self):
        path = make_docx(self.dir)
        code, obj = run([str(path)])
        self.assertEqual(code, 0, obj)
        self.assertEqual(obj["format"], "docx")
        self.assertEqual(obj["paragraph_count"], 4)
        self.assertEqual(obj["paragraphs"][0], {"index": 0, "text": "Первый абзац"})
        self.assertEqual(obj["paragraphs"][1]["text"], "Второй\tс табом")
        self.assertEqual(obj["paragraphs"][2]["text"], "\nПосле переноса")
        self.assertEqual(obj["paragraphs"][3], {"index": 3, "text": ""})

    def test_docx_zipbomb_and_dtd_rejected(self):
        bomb = self.dir / "bomb.docx"
        with zipfile.ZipFile(bomb, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr(
                "word/document.xml", b"<w/" + b"a" * (21 * 1024 * 1024)
            )
        code, obj = run([str(bomb)])
        self.assertEqual(code, 1)
        self.assertIn("бомб", obj["error"].lower())
        dtd = self.dir / "dtd.docx"
        with zipfile.ZipFile(dtd, "w") as archive:
            archive.writestr(
                "word/document.xml",
                '<?xml version="1.0"?><!DOCTYPE w:document '
                '[<!ENTITY x "y">]><w:document '
                'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
                "<w:body/></w:document>",
            )
        code, obj = run([str(dtd)])
        self.assertEqual(code, 1)
        self.assertIn("DTD", obj["error"])

    def test_docx_utf16_dtd_rejected(self):
        xml = (
            '<?xml version="1.0"?><!DOCTYPE w:document '
            '[<!ENTITY x "y">]><w:document '
            'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
            "<w:body/></w:document>"
        )
        path = self.dir / "utf16.docx"
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("word/document.xml", xml.encode("utf-16"))
        code, obj = run([str(path)])
        self.assertEqual(code, 1)
        self.assertIn("UTF-8", obj["error"])

    def test_unsupported_and_missing(self):
        pdf = self.dir / "doc.pdf"
        pdf.write_bytes(b"%PDF-1.4 fake")
        code, obj = run([str(pdf)])
        self.assertEqual(code, 1)
        self.assertIn("TXT", obj["error"])
        audio = self.dir / "voice.mp3"
        audio.write_bytes(b"ID3")
        code, obj = run([str(audio)])
        self.assertEqual(code, 1)
        code, obj = run([str(self.dir / "none.txt")])
        self.assertEqual(code, 1)
        code, obj = run([str(self.dir)])
        self.assertEqual(code, 1)
        big = self.dir / "huge.txt"
        big.write_bytes(b"x" * (5 * 1024 * 1024 + 1))
        code, obj = run([str(big)])
        self.assertEqual(code, 1)
        self.assertIn("5 МиБ", obj["error"])

    def test_docx_missing_document_xml(self):
        path = self.dir / "fake.docx"
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("readme.txt", "not a docx")
        code, obj = run([str(path)])
        self.assertEqual(code, 1)
        self.assertIn("document.xml", obj["error"])


if __name__ == "__main__":
    unittest.main()
