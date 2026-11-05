# VeriLoom deployment checklist

**Status:** preparation only. No hosted API, worker, or frontend deployment is verified. The browser run below used local services and the existing Render demo database.

## Reference boundaries

The [API contract](Product%20specification/API%20Contracts.md) owns HTTP behavior, authentication, and states. The [database design](Product%20specification/Database%20Design%20and%20ERD.md) owns migrations and tenant constraints. This file owns service setup and the release smoke test. Use [.env.example](../.env.example) for variable names; enter real values only in each host's secret settings.

## Release gate before the judges' demo

The 2026-11-05 synthetic browser run completed the Admin source inspection → Creator submit → Reviewer citation/chat/comment/change request → Creator revision/resubmit → Reviewer approval loop. It also exposed two AI quality defects: an explicit approved factual sentence was called unsupported in a follow-up, and the same sentence received a high-severity finding although the finding explanation admitted it was approved. The human reviewer correctly recorded an override. Fix and independently evaluate those defects before describing automated finding or answer quality as reliable. The latest browser run did not re-upload a source; the already-approved Markdown source was inspected and used by the new review.

## 1. Render database

- The existing demo database reported Alembic revision `007_provider_attribution` on 2026-11-05. Inspect the dashboard and migration version again before any schema change. The current follow-up routing patch requires no migration.
- Use the database's **internal** URL for services in the same Render region. Keep the external URL in ignored local settings only. Keep demo records separate from any real customer organisation.
- Check the database's expiration date before the event. Render's Free PostgreSQL database expires after 30 days and has no managed backups; export or upgrade before that date if the demo records must survive.

## 2. Render API web service

- Connect the GitHub repository and select `backend` as the root directory, Python runtime, and Python `3.12.10` (`PYTHON_VERSION=3.12.10`).
- Build command: `pip install -r requirements.txt`
- Start command: `uvicorn app.main:app --host 0.0.0.0 --port $PORT`
- Set `DATABASE_URL`, `SESSION_SECRET`, `APP_ORIGIN`, and the four `R2_*` variables in Render's environment settings. `APP_ORIGIN` must be the exact final Vercel HTTPS origin, with no trailing slash. Use a stable Vercel domain for this one-origin MVP.
- Configure `/readyz` as the health check, and verify `/healthz` and `/readyz` both return 200 after deployment. The API should have one instance while its current rate limiter is per process.

## 3. Render background worker

- Create a separate Background Worker from the same repository, root `backend`, Python `3.12.10`, build `pip install -r requirements.txt`, start `python -m app.jobs.worker`.
- Set the same `DATABASE_URL`, `SESSION_SECRET`, and private `R2_*` values, plus `SARVAM_API_KEY`, `SARVAM_MODEL`, and `EMBEDDING_MODEL=intfloat/multilingual-e5-small`. Set `INDIEROUTER_*` only if the approved fallback is wanted; keep `RERANKER_ENABLED=false`.
- Render has no Free Background Worker plan. The local E5-loaded process used about 807 MB, excluding a deployed peak; a 512 MB worker plan is therefore unsuitable without separate proof. Start with a 2 GB worker and inspect actual memory, cold-start time, job retries, and costs. The owner's Render credit does not itself establish that a payment method is unnecessary.

## 4. Vercel frontend

- Import the same GitHub repository with root directory `frontend`, framework Vite, build `npm run build`, output `dist`. Use Node 20.19+ or a compatible current version.
- After the Render API has a stable HTTPS URL, add `frontend/vercel.json` with the API rewrite **before** the SPA fallback, replacing the example hostname:

```json
{
  "$schema": "https://openapi.vercel.sh/vercel.json",
  "rewrites": [
    { "source": "/api/:path*", "destination": "https://YOUR-API.onrender.com/api/:path*" },
    { "source": "/(.*)", "destination": "/index.html" }
  ]
}
```

- Keep `VITE_API_BASE=/api/v1`; no provider or database secret belongs in Vercel. The browser must use the same-origin `/api` proxy so the secure, HttpOnly session cookie and CSRF checks work. Set Render `APP_ORIGIN` to the exact deployed Vercel origin, then redeploy the API if it changed.

## 5. Hosted smoke test

1. Open a deep frontend URL directly and refresh it to verify the SPA rewrite.
2. Register or sign in with synthetic demo accounts; verify `/api/v1/auth/me`, secure session persistence, logout, and Origin/CSRF behavior over HTTPS.
3. Upload and approve one synthetic `.md` source; confirm the worker makes it `ready`, then submit a new synthetic draft. Inspect citations, follow-up responses, comments, change request, resubmission, human approval, history after refresh, and a 390 px mobile view.
4. Inspect Render API/worker logs and job states for retries, memory pressure, failed outputs, and provider attribution. Do not call the release quality-verified while the two recorded AI defects remain unresolved.

Current platform references: [Render FastAPI](https://render.com/docs/deploy-fastapi), [Render workers](https://render.com/docs/background-workers), [Render compute plans](https://render.com/docs/compute-plans), [Render PostgreSQL connections](https://render.com/docs/postgresql-creating-connecting), [Render Free database limits](https://render.com/docs/free), [Vercel monorepo roots](https://vercel.com/docs/monorepos), [Vercel Vite SPA routing](https://vercel.com/docs/frameworks/frontend/vite), and [Vercel rewrites](https://vercel.com/docs/routing/rewrites).
