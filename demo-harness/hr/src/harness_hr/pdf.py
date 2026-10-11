"""Write a plain text PDF with no dependencies.

Output is deterministic: the same text always yields the same bytes, so the content hash
changes only when the text does. Each page is Helvetica 10 pt with extractable text.
"""

import textwrap

LINES_PER_PAGE = 52
WRAP = 92


def _escape(text: str) -> str:
    text = text.encode("latin-1", "replace").decode("latin-1")
    return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def _lines(markdown: str) -> list[str]:
    lines: list[str] = []
    for raw in markdown.splitlines():
        stripped = raw.lstrip("#").strip() if raw.startswith("#") else raw.rstrip()
        if not stripped:
            lines.append("")
            continue
        lines.extend(textwrap.wrap(stripped, WRAP, subsequent_indent="  ") or [""])
    return lines


def render_pdf(markdown: str) -> bytes:
    lines = _lines(markdown)
    pages = [lines[i : i + LINES_PER_PAGE] for i in range(0, len(lines), LINES_PER_PAGE)] or [[]]
    objects: list[bytes] = []
    # 1 catalog, 2 pages, 3 font, then a page and a content stream per page.
    page_ids = [4 + 2 * index for index in range(len(pages))]
    objects.append(b"<< /Type /Catalog /Pages 2 0 R >>")
    kids = " ".join(f"{pid} 0 R" for pid in page_ids)
    objects.append(f"<< /Type /Pages /Kids [{kids}] /Count {len(pages)} >>".encode())
    objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    for index, page in enumerate(pages):
        content_id = page_ids[index] + 1
        objects.append(
            (
                "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
                f"/Resources << /Font << /F1 3 0 R >> >> /Contents {content_id} 0 R >>"
            ).encode()
        )
        stream = ["BT", "/F1 10 Tf", "13 TL", "50 750 Td"]
        for line in page:
            stream.append(f"({_escape(line)}) Tj T*")
        stream.append("ET")
        data = "\n".join(stream).encode("latin-1")
        objects.append(b"<< /Length %d >>\nstream\n" % len(data) + data + b"\nendstream")
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % number + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    for offset in offsets:
        out += b"%010d 00000 n \n" % offset
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (
        len(objects) + 1,
        xref,
    )
    return bytes(out)
