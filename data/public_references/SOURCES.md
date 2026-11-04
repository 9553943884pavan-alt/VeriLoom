# Public Reference Collection

Collected on 2026-11-03 from seven official source pages. The nine JSONL records contain short original summaries with provenance, not downloaded policy documents or brand assets.

| Record | Topic | Official source |
|---|---|---|
| PUB-01 | Informative images need meaningful alternatives | [W3C Web Accessibility Initiative](https://www.w3.org/WAI/tutorials/images/informative/) |
| PUB-02 | Decorative images and empty alt attributes | [W3C Web Accessibility Initiative](https://www.w3.org/WAI/tutorials/images/decorative/) |
| PUB-03 | Text contrast thresholds and exceptions | [W3C Web Accessibility Initiative](https://www.w3.org/WAI/WCAG22/Understanding/contrast-minimum.html) |
| PUB-04 | Communicating information beyond colour | [W3C Web Accessibility Initiative](https://www.w3.org/WAI/WCAG22/Understanding/use-of-color.html) |
| PUB-05 | Stable organisational voice and adaptable tone | [UK Home Office](https://design.homeoffice.gov.uk/design-and-content/content/voice-and-tone) |
| PUB-06 | Plain language for the intended audience | [Digital.gov](https://digital.gov/style-guide) |
| PUB-07 | Headlines should convey context without overpromising | [Digital.gov](https://digital.gov/style-guide) |
| PUB-08 | Brand-specific clear-space measurement | [Chicago Bears](https://www.chicagobears.com/about/brand-guidelines) |
| PUB-09 | Minimum logo size and approved alternatives | [Chicago Bears](https://www.chicagobears.com/about/brand-guidelines) |

## Direct use and approval boundary

Read `references.jsonl` as UTF-8 JSON Lines: each nonempty line is one object. Each record has a stable `record_id`, topic, short summary, source URL, publisher, access date, and reuse/adoption metadata.

These records deliberately have `status: reference_only`, `ingestible: false`, `organisation_id: null`, and `adoption_required: true`. They are useful for a research/reference screen or for designing policy import. An administrator must review a source and explicitly adopt an appropriate rule into a named organisation before the application treats it as approved brand guidance. Preserve the external source URL and create a separate organisation-scoped record when doing so.

The W3C Understanding pages and tutorials explain accessibility criteria and techniques; they are not substitutes for a complete conformance assessment. A content review tool cannot certify WCAG conformance from these examples alone. Visual criteria require the actual rendered content, context, dimensions, and colours; text descriptions alone cannot establish a visual result.

The Home Office, Digital.gov, and Chicago Bears examples are publisher-specific. Their voice, title casing, mark sizes, and clear-space values do not automatically apply to Nook & Kettle or another organisation.

## Provenance and reuse

No original website, image, logo, or full document was downloaded into this collection. Text here is a brief original paraphrase with a link to the supporting page. `source_terms_url` is null because this collection does not claim a verified licence for redistribution of the underlying pages or assets; the `license_note` explicitly limits that claim. Review the publisher's current terms before copying source material or reusing marks.

Public-reference summaries are not real user interviews, product performance results, or customer validation. Access dates record this collection event; they are not claims about each publisher's revision date.

Additional research evidence for Round 1 is described in [PROBLEM_EVIDENCE.md](PROBLEM_EVIDENCE.md) and stored as problem_evidence.jsonl. It is excluded from brand-source ingestion.
