"""Format-specific chunkers.

Markdown splits on headings and keeps the heading path. A PDF from the HR
harness has no heading marks left, so it becomes one chunk per paragraph.
A structured record is one chunk, rendered from the contract's in-scope
fields. salary, compensation, ssn, and national_id are never rendered.
"""

import json
import re
from dataclasses import dataclass

from shared.contracts.source_contract import SourceContract

MARKDOWN = "markdown-headings-v1"
RECORD = "record-v1"
PDF = "pdf-text-v1"
FORBIDDEN = frozenset({"salary", "compensation", "ssn", "national_id"})
HEADING = re.compile(r"^(#{1,6})[ \t]+(.+)$")
PDF_LINE = re.compile(r"\((?:\\.|[^\\)])*\) Tj")


@dataclass(frozen=True)
class ChunkDraft:
    index: int
    section_path: str
    text: str
    chunker_version: str


@dataclass(frozen=True)
class Parsed:
    object_type: str
    chunks: tuple[ChunkDraft, ...]
    record: dict
    title: str


def parse_object(
    object_type: str,
    body: bytes,
    contract: SourceContract,
) -> Parsed:
    if object_type in {"person", "deal", "epic"} or _looks_like_json(body):
        record = _public_record(json.loads(body), contract)
        title = str(
            record.get("full_name")
            or record.get("title")
            or record.get("policy_id")
            or record.get("person_id")
            or record.get("deal_id")
            or record.get("epic_id")
            or object_type
        )
        return Parsed(object_type, tuple(chunk_record(record, contract, object_type, title)), record, title)
    if body.startswith(b"%PDF"):
        chunks, title = chunk_pdf(body)
        return Parsed(object_type, tuple(chunks), {}, title)
    text = body.decode("utf-8")
    chunks, title = chunk_markdown(text)
    return Parsed(object_type, tuple(chunks), {}, title)


def chunk_markdown(body: str) -> tuple[list[ChunkDraft], str]:
    drafts: list[ChunkDraft] = []
    stack: list[tuple[int, str]] = []
    title = ""
    for block in re.split(r"(?=^#{1,6}[ \t]+)", body, flags=re.MULTILINE):
        block = block.strip()
        if not block:
            continue
        first, _, rest = block.partition("\n")
        match = HEADING.match(first)
        if match is None:
            path = title or "document"
            drafts.append(ChunkDraft(len(drafts), path, f"{path}\n\n{block}", MARKDOWN))
            continue
        level = len(match.group(1))
        heading = match.group(2).strip()
        if level == 1 and not title:
            title = heading
        stack = [item for item in stack if item[0] < level]
        stack.append((level, heading))
        path = " / ".join(name for _, name in stack)
        prose = rest.strip()
        if not prose:
            continue
        drafts.append(ChunkDraft(len(drafts), path, f"{path}\n\n{prose}", MARKDOWN))
    if not drafts and body.strip():
        drafts.append(ChunkDraft(0, title or "document", body.strip(), MARKDOWN))
    return drafts, title or "document"


def chunk_pdf(body: bytes) -> tuple[list[ChunkDraft], str]:
    text = _pdf_text(body)
    paragraphs = [part.strip() for part in re.split(r"\n\s*\n", text) if part.strip()]
    if not paragraphs:
        raise ValueError("pdf had no extractable text")
    title = paragraphs[0][:120]
    drafts = [
        ChunkDraft(index, title if index == 0 else f"{title} / {index}", f"{title}\n\n{paragraph}", PDF)
        for index, paragraph in enumerate(paragraphs)
    ]
    return drafts, title


def chunk_record(
    record: dict,
    contract: SourceContract,
    object_type: str,
    title: str,
) -> list[ChunkDraft]:
    lines = []
    for key in contract.in_scope_fields:
        if key.lower() in FORBIDDEN or key not in record:
            continue
        value = record[key]
        if isinstance(value, list):
            value = ", ".join(str(item) for item in value)
        if value is None:
            continue
        lines.append(f"{key}: {value}")
    path = f"{contract.display_name} / {object_type} / {title}"
    text = path + "\n\n" + "\n".join(lines)
    return [ChunkDraft(0, path, text, RECORD)]


def _public_record(record: dict, contract: SourceContract) -> dict:
    # Drop forbidden fields. Keep epic_id, team, and manager_id even when they
    # are outside in_scope_fields: the graph edges come from them.
    allowed = {name.lower() for name in contract.in_scope_fields} | {"epic_id", "team", "manager_id"}
    return {
        key: value
        for key, value in record.items()
        if key.lower() in allowed and key.lower() not in FORBIDDEN
    }


def _looks_like_json(body: bytes) -> bool:
    stripped = body.lstrip()
    return stripped.startswith((b"{", b"["))


def _pdf_text(body: bytes) -> str:
    raw = body.decode("latin-1", errors="replace")
    lines = []
    for match in PDF_LINE.finditer(raw):
        text = match.group(0)[1:-4]
        text = text.replace(r"\(", "(").replace(r"\)", ")").replace(r"\\", "\\")
        lines.append(text)
    return "\n".join(lines)
