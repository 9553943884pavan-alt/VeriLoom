"""Rebuild synthetic source JSONL. Run: python data/knowledge/build_records.py"""

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUTPUT = ROOT / "data/knowledge"


def plain(text):
    return text.replace("**", "").replace("`", "").strip()


def record(document_id, local_id, title, text, category, source_path):
    return {
        "record_id": f"{document_id}#{local_id}",
        "organisation_id": "nook-kettle",
        "document_id": document_id,
        "local_id": local_id,
        "title": title,
        "text": plain(text),
        "category": category,
        "source_path": source_path,
        "source_version": "1.0",
        "status": "approved",
        "effective_from": "2026-11-03",
        "effective_to": None,
        "data_kind": "synthetic_demo",
        "ingestible": True,
    }


def build():
    records = []
    source_path = "data/brand_sources/brand_guidelines.md"
    content = (ROOT / source_path).read_text(encoding="utf-8-sig")
    headings = list(re.finditer(r"^### `([^`]+)` — (.+)$", content, re.M))
    categories = {
        "MESSAGE": "messaging", "VOICE": "tone", "CLAIM": "claims",
        "TERM": "terminology", "VISUAL": "visual", "ACCESS": "accessibility",
        "GOV": "governance",
    }
    for heading in headings:
        rest = content[heading.end():]
        next_heading = re.search(r"^#{1,3} ", rest, re.M)
        body = rest[:next_heading.start()] if next_heading else rest
        local_id, title = heading.groups()
        records.append(record("NK-BRAND-001", local_id, title, body,
                              categories[local_id.split("-")[0]], source_path))

    source_path = "data/brand_sources/approved_facts.md"
    fact_titles = [
        "Fictional organisation identity", "Approved café locations",
        "Opening days and hours", "Dine-in and takeaway services",
        "No approved awards or endorsements", "Cardamom Chai price",
        "Masala Chai price", "Jaggery Cold Coffee price", "Menu availability",
        "Unspecified dietary and allergen information", "No approved promotions",
    ]
    for line in (ROOT / source_path).read_text(encoding="utf-8-sig").splitlines():
        if not line.startswith("| `FACT-"):
            continue
        local_id, fact, note = [part.strip() for part in line.strip("|").split("|")]
        local_id = plain(local_id)
        title = fact_titles[int(local_id.split("-")[1]) - 1]
        records.append(record("NK-FACTS-001", local_id, title,
                              f"{fact}\nUsage notes: {note}", "facts", source_path))

    source_path = "data/brand_sources/terminology.md"
    for line in (ROOT / source_path).read_text(encoding="utf-8-sig").splitlines():
        if not line.startswith("| `TERM-"):
            continue
        local_id, term, alternatives, note = [part.strip() for part in line.strip("|").split("|")]
        records.append(record("NK-TERMS-001", plain(local_id), f"Preferred term: {term}",
                              f"Preferred term: {term}.\nAvoid or correct: {alternatives}.\n{note}",
                              "terminology", source_path))

    source_path = "data/knowledge/isolation_fixture_sources.md"
    archived = record(
        "NK-FACTS-ARCHIVE-001", "FACT-06", "Archived Cardamom Chai price",
        "Cardamom Chai was priced at ₹99 per cup during the synthetic historical "
        "fixture period. This archived value must not answer a current-price "
        "question dated 2026-11-03 or later. See NK-FACTS-001#FACT-06 for the "
        "current approved price.", "facts", source_path,
    )
    archived.update(status="archived", effective_from="2026-10-01",
                    effective_to="2026-11-03", ingestible=False)
    other_tenant = record(
        "CC-FACTS-001", "FACT-06", "Cedar Cup Cardamom Chai price",
        "Cedar Cup's Cardamom Chai is priced at ₹80 per cup. Cedar Cup is a "
        "separate fictional organisation. Its facts must not answer Nook & Kettle questions.",
        "facts", source_path,
    )
    other_tenant.update(organisation_id="cedar-cup", ingestible=False)
    OUTPUT.mkdir(parents=True, exist_ok=True)
    for filename, rows in [("records.jsonl", records),
                           ("isolation_fixtures.jsonl", [archived, other_tenant])]:
        output = "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows)
        (OUTPUT / filename).write_text(output, encoding="utf-8")
        print(f"{filename}: {len(rows)} records")


if __name__ == "__main__":
    build()
