"""Word export is editable text, bounded, explicit and free of external links."""
from io import BytesIO
import unittest
from xml.etree import ElementTree as ET
from zipfile import ZipFile
from unittest.mock import patch

import document_export as export
import server
from test_offline import request

ORIGIN = {"Origin": "http://127.0.0.1:4700"}


class DocumentExportTests(unittest.TestCase):
    def test_editable_unicode_text_and_xml_escaping(self):
        content = export.word_document('Nivaen’s findings', 'Café 👋 <script> & text\nSecond paragraph')
        with ZipFile(BytesIO(content)) as archive:
            self.assertEqual(set(archive.namelist()), {'[Content_Types].xml', '_rels/.rels', 'word/document.xml'})
            for name in archive.namelist():
                ET.fromstring(archive.read(name))
            root = ET.fromstring(archive.read('word/document.xml'))
            texts = [item.text for item in root.iter('{' + export.WORD_NS + '}t')]
            self.assertEqual(texts, ['Nivaen’s findings', 'Café 👋 <script> & text', 'Second paragraph'])
            self.assertNotIn('TargetMode="External"', archive.read('_rels/.rels').decode())

    def test_invalid_and_oversized_text_is_rejected(self):
        for title, text in (('', 'answer'), ('title', ''), (True, 'answer'), ('title', []),
                ('x' * (export.MAX_TITLE_CHARS + 1), 'a'), ('title', 'x' * (export.MAX_TEXT_CHARS + 1)),
                ('title', 'bad\0text'), ('title', '\ud800')):
            with self.subTest(title=type(title).__name__, text=type(text).__name__), self.assertRaises(ValueError):
                export.word_document(title, text)

    def test_http_download_has_real_type_and_attachment(self):
        with patch.object(server, 'load_config', side_effect=AssertionError('export read config')):
            status, headers, body = request('/export/word', 'POST', {'title': 'Answer', 'text': 'Verified text'}, ORIGIN)
        self.assertEqual(status, 200)
        self.assertIn(export.CONTENT_TYPE, headers)
        self.assertIn('attachment; filename="Jarvis answer.docx"', headers)
        self.assertTrue(body.startswith(b'PK'))

    def test_http_rejects_missing_origin_and_unknown_fields(self):
        body = {'title': 'Answer', 'text': 'Verified text'}
        self.assertEqual(request('/export/word', 'POST', body)[0], 403)
        self.assertEqual(request('/export/word', 'POST', dict(body, path='/tmp/anything'), ORIGIN)[0], 400)


if __name__ == '__main__':
    unittest.main()
