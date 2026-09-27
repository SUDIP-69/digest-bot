from daily_digest import extract_pdf_text


def make_pdf_with_text(text: str) -> bytes:
    escaped = text.replace('\\', '\\\\').replace('(', '\\(').replace(')', '\\)')
    raw_text = escaped.encode('latin-1', 'replace')
    stream = b"BT /F1 18 Tf 50 700 Td (" + raw_text + b") Tj ET"
    contents = b"<< /Length " + str(len(stream)).encode('ascii') + b" >>\nstream\n" + stream + b"\nendstream"
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 400 800] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
        contents,
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]

    output = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for index, obj in enumerate(objects, 1):
        offsets.append(len(output))
        output.extend(f"{index} 0 obj\n".encode('latin-1'))
        output.extend(obj)
        output.extend(b"\nendobj\n")

    xref_start = len(output)
    output.extend(f"xref\n0 {len(objects) + 1}\n".encode('latin-1'))
    output.extend(b"0000000000 65535 f \n")
    for off in offsets[1:]:
        output.extend(f"{off:010d} 00000 n \n".encode('latin-1'))
    output.extend(f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref_start}\n%%EOF\n".encode('latin-1'))
    return bytes(output)


def test_extract_pdf_text_reads_text_from_pdf():
    pdf_bytes = make_pdf_with_text('SSC Admit Card exam city notice')
    text = extract_pdf_text(pdf_bytes)
    assert 'SSC Admit Card' in text
    assert 'exam city' in text
