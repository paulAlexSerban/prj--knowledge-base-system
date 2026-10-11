"""Chunkers. A forbidden field must not survive into the text."""

from pathlib import Path

from knowledge_store.chunkers import MARKDOWN, PDF, RECORD, parse_object
from shared.contracts.validate import load_contracts


def _hr():
    root = Path(__file__).resolve().parents[4]
    found = load_contracts(root / "infrastructure" / "config" / "sources")
    return next(item for item in found if item.source_id == "hr")


POLICY = """# Leave

## Eligibility

Everyone in Germany.

## Pay

Full salary for 16 weeks.
"""


def test_markdown_splits_on_headings_and_keeps_the_path() -> None:
    parsed = parse_object("policy", POLICY.encode(), _hr())
    assert parsed.title == "Leave"
    assert [chunk.section_path for chunk in parsed.chunks] == [
        "Leave / Eligibility",
        "Leave / Pay",
    ]
    assert parsed.chunks[0].chunker_version == MARKDOWN
    assert "Everyone in Germany." in parsed.chunks[0].text
    assert "Eligibility" in parsed.chunks[0].text


def test_record_drops_salary_and_national_id() -> None:
    body = (
        b'{"person_id":"emp-1","full_name":"Ada Lovelace","team":"People",'
        b'"email":"ada@example.com","role":"Head","salary":90000,'
        b'"national_id":"NID-1","version":"1"}'
    )
    parsed = parse_object("person", body, _hr())
    assert parsed.chunks[0].chunker_version == RECORD
    assert "Ada Lovelace" in parsed.chunks[0].text
    assert "90000" not in parsed.chunks[0].text
    assert "NID-1" not in parsed.chunks[0].text
    assert "salary" not in parsed.record
    assert "national_id" not in parsed.record


def test_pdf_text_is_chunked() -> None:
    from harness_hr.pdf import render_pdf

    parsed = parse_object("policy", render_pdf(POLICY), _hr())
    assert parsed.chunks
    assert parsed.chunks[0].chunker_version == PDF
    joined = "\n".join(chunk.text for chunk in parsed.chunks)
    assert "Germany" in joined
    assert "90000" not in joined
