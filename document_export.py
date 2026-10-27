"""Create a small editable Word document in memory using Office Open XML."""
from io import BytesIO
from xml.etree import ElementTree as ET
from zipfile import ZipFile, ZIP_DEFLATED

MAX_TEXT_CHARS = 40000
MAX_TITLE_CHARS = 160
MAX_REQUEST_BYTES = MAX_TEXT_CHARS * 6 + 4096
CONTENT_TYPE = 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'
WORD_NS = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'
XML_SPACE = '{http://www.w3.org/XML/1998/namespace}space'
INVALID_XML = frozenset(chr(value) for value in range(32) if value not in (9, 10, 13))
PAGE_WIDTH = '12240'
PAGE_HEIGHT = '15840'
PAGE_MARGIN = '1440'
TITLE_SIZE = '32'


def word_document(title, text):
    if (not isinstance(title, str) or not title.strip() or len(title) > MAX_TITLE_CHARS
            or not isinstance(text, str) or not text.strip() or len(text) > MAX_TEXT_CHARS
            or any(character in INVALID_XML or 0xD800 <= ord(character) <= 0xDFFF
                or ord(character) in (0xFFFE, 0xFFFF) for character in title + text)):
        raise ValueError('Supply a title and answer within the export limits.')
    ET.register_namespace('w', WORD_NS)
    def node(parent, tag, **attributes):
        return ET.SubElement(parent, '{' + WORD_NS + '}' + tag,
            {'{' + WORD_NS + '}' + key: value for key, value in attributes.items()})
    document = ET.Element('{' + WORD_NS + '}document')
    body = node(document, 'body')
    def paragraph(value, heading=False):
        p = node(body, 'p')
        run = node(p, 'r')
        if heading:
            properties = node(run, 'rPr')
            node(properties, 'b')
            node(properties, 'sz', val=TITLE_SIZE)
        content = node(run, 't')
        content.set(XML_SPACE, 'preserve')
        content.text = value
    paragraph(title.strip(), heading=True)
    for line in text.replace('\r\n', '\n').replace('\r', '\n').split('\n'):
        paragraph(line)
    section = node(body, 'sectPr')
    node(section, 'pgSz', w=PAGE_WIDTH, h=PAGE_HEIGHT)
    node(section, 'pgMar', top=PAGE_MARGIN, right=PAGE_MARGIN, bottom=PAGE_MARGIN, left=PAGE_MARGIN)
    output = BytesIO()
    with ZipFile(output, 'w', compression=ZIP_DEFLATED) as archive:
        archive.writestr('[Content_Types].xml', '<?xml version="1.0" encoding="UTF-8"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
            '</Types>')
        archive.writestr('_rels/.rels', '<?xml version="1.0" encoding="UTF-8"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>'
            '</Relationships>')
        archive.writestr('word/document.xml', ET.tostring(document, encoding='utf-8', xml_declaration=True))
    return output.getvalue()
