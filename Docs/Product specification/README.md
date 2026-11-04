# VeriLoom product specifications

These documents define the Round 2 implementation contract. The API contract is the public boundary; the database, frontend, RAG, and prompt documents detail how each part fulfils it.

The direct MVP accepts text guidance and text drafts. The synthetic data pack includes future-format `image_metadata` cases; use only the text cases and their linked follow-ups when reporting text-only review results. The [data audit](../../data/AUDIT.md) describes the pack's provenance and limits.

Use the root [`.env.example`](../../.env.example) as the configuration checklist. It contains placeholders only; keep real credentials in a local `.env` or the deployment secret settings.

The proposed backend, worker, frontend, and test file layout is in [PROJECT_STRUCTURE.md](../PROJECT_STRUCTURE.md). It marks existing files separately from implementation files still to create.

| Document | Purpose |
|---|---|
| [API Contracts.md](API%20Contracts.md) | HTTP endpoints, permissions, request/response shapes, states, errors, and service boundaries. |
| [Database Design and ERD.md](Database%20Design%20and%20ERD.md) | Tables, relationships, indexes, migrations, and lifecycle. |
| [Frontend Architecture Specification.md](Frontend%20Architecture%20Specification.md) | React routes, components, state, validation, and API integration. |
| [RAG Architecture Specification.md](RAG%20Architecture%20Specification.md) | Indexing, retrieval, reranking, evidence, and evaluation. |
| [Prompt Engineering Specification.md](Prompt%20Engineering%20Specification.md) | Prompt templates, variables, versions, constraints, and validation. |

## Reference boundaries

- Product intent and scope: [PRESENTATION.md](../PRESENTATION.md) and [Problem_statement.md](../Problem_statement.md).
- Fixture data and provenance: [data/README.md](../../data/README.md) and [data/AUDIT.md](../../data/AUDIT.md).
- These are specifications. Implemented behavior must be verified against generated OpenAPI, migrations, UI tests, retrieval evaluation, and deployed smoke tests.
