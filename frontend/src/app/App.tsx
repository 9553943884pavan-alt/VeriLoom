import { useEffect, useState } from "react";
import {
  Link,
  Navigate,
  Outlet,
  Route,
  Routes,
  useNavigate,
  useParams,
} from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api, ApiError, setCsrfToken } from "../lib/api/client";
import type { Membership, Page, Session } from "../lib/types";
import {
  ActionForm,
  ErrorNotice,
  Field,
  More,
  usePages,
} from "../components/ui";
import { OrgContext, SessionContext, useOrg, useSession } from "./context";
import { Auth } from "../features/auth/Auth";
import { Sources } from "../features/sources/Sources";
import {
  AssignedDraft,
  DraftEditor,
  DraftList,
} from "../features/drafts/Drafts";
import { ReviewWorkspace } from "../features/reviews/Reviews";
type Organisation = Membership & { corpus_version: number };
function RequireSession() {
  const session = useQuery({
    queryKey: ["session"],
    queryFn: async () => {
      const result = await api<Session>("/auth/me");
      setCsrfToken(result.csrf_token);
      return result;
    },
    retry: false,
  });
  useEffect(() => {
    if (session.error) setCsrfToken("");
  }, [session.error]);
  if (session.isPending)
    return <main aria-live="polite">Loading your workspace…</main>;
  if (session.error instanceof ApiError && session.error.status === 401)
    return <Navigate to="/login" replace />;
  if (session.error)
    return (
      <main>
        <ErrorNotice error={session.error} />
        <button onClick={() => void session.refetch()}>Retry</button>
      </main>
    );
  return (
    <SessionContext.Provider value={session.data!}>
      <Outlet />
    </SessionContext.Provider>
  );
}
function Organisations() {
  const session = useSession();
  const navigate = useNavigate();
  const client = useQueryClient();
  const orgs = usePages<Organisation>("/organisations");
  return (
    <main className="orgs">
      <h1>Your organisations</h1>
      <p>Welcome, {session.display_name}. Choose a workspace to continue.</p>
      <ErrorNotice error={orgs.error} />
      <div className="grid">
        {orgs.data?.pages
          .flatMap((p) => p.items)
          .map((o) => (
            <Link
              className="card"
              to={`/org/${o.org_id}/overview`}
              key={o.org_id}
            >
              <h2>{o.name}</h2>
              <p>{o.roles.join(" · ")}</p>
              <span>Open workspace →</span>
            </Link>
          ))}
      </div>
      <More {...orgs} />
      <section className="card">
        <h2>Create an organisation</h2>
        <ActionForm
          label="Create workspace"
          onSubmit={async (f) => {
            const o = await api<Organisation>("/organisations", "POST", {
              name: f.get("name"),
              time_zone: f.get("time_zone"),
            });
            await client.invalidateQueries({ queryKey: ["session"] });
            navigate(`/org/${o.org_id}/overview`);
          }}
        >
          <Field label="Organisation name">
            <input name="name" required maxLength={120} />
          </Field>
          <Field label="Time zone">
            <input
              name="time_zone"
              required
              defaultValue={Intl.DateTimeFormat().resolvedOptions().timeZone}
            />
          </Field>
        </ActionForm>
      </section>
      <section className="card">
        <h2>Join with an invitation</h2>
        <ActionForm
          label="Accept invitation"
          onSubmit={async (f) => {
            await api("/invitations/accept", "POST", {
              invitation_token: f.get("invitation_token"),
            });
            await client.invalidateQueries({ queryKey: ["session"] });
          }}
        >
          <Field label="Invitation token">
            <input required name="invitation_token" autoComplete="off" />
          </Field>
        </ActionForm>
      </section>
    </main>
  );
}
function Shell() {
  const { orgId } = useParams();
  const session = useSession();
  const client = useQueryClient();
  const navigate = useNavigate();
  const [error, setError] = useState<unknown>();
  const orgs = useQuery({
    queryKey: ["organisations"],
    queryFn: () => api<Page<Organisation>>("/organisations?limit=100"),
  });
  const membership = session.memberships.find((m) => m.org_id === orgId);
  const org = orgs.data?.items.find((m) => m.org_id === orgId);
  if (!membership)
    return (
      <main>
        <h1>Access denied</h1>
        <Link to="/organisations">Choose an organisation</Link>
      </main>
    );
  if (!org)
    return (
      <main>
        <ErrorNotice error={orgs.error} />
        Loading organisation…
      </main>
    );
  return (
    <OrgContext.Provider value={org}>
      <div className="shell">
        <aside>
          <Link className="brand" to="/organisations">
            ◈ VeriLoom
          </Link>
          <p className="eyebrow">Brand review workspace</p>
          <Field label="Organisation">
            <select
              value={orgId}
              onChange={(e) => navigate(`/org/${e.target.value}/overview`)}
            >
              {orgs.data?.items.map((o) => (
                <option key={o.org_id} value={o.org_id}>
                  {o.name}
                </option>
              ))}
            </select>
          </Field>
          <nav aria-label="Workspace">
            <Link to="overview">Overview</Link>
            {org.roles.includes("admin") && (
              <>
                <Link to="admin/sources">Source library</Link>
                <Link to="admin/members">People & invitations</Link>
              </>
            )}
            {org.roles.includes("creator") && (
              <Link to="creator/drafts">My drafts</Link>
            )}
            {org.roles.includes("reviewer") && (
              <Link to="reviewer/inbox">Review inbox</Link>
            )}
            <Link to="/organisations">All organisations</Link>
          </nav>
          <div className="profile">
            <strong>{session.display_name}</strong>
            <small>{org.roles.join(" · ")}</small>
            <button
              className="secondary"
              onClick={async () => {
                try {
                  await api("/auth/logout", "POST", {});
                  setCsrfToken("");
                  client.clear();
                  navigate("/login");
                } catch (e) {
                  setError(e);
                }
              }}
            >
              Sign out
            </button>
            <ErrorNotice error={error} />
          </div>
        </aside>
        <main>
          <Outlet key={org.org_id} />
        </main>
      </div>
    </OrgContext.Provider>
  );
}
function Overview() {
  const org = useOrg();
  return (
    <>
      <p className="eyebrow">Your workspace</p>
      <h1>
        Words with evidence.
        <br />
        Decisions with confidence.
      </h1>
      <p className="intro">
        Upload approved guidance, shape a draft, and bring a human reviewer into
        the loop.
      </p>
      <div className="grid">
        {org.roles.includes("admin") && (
          <Link className="card" to="../admin/sources">
            <span className="step">01 / KNOWLEDGE</span>
            <h2>Build your source library</h2>
            <p>Approve text guidance before it can inform a review.</p>
          </Link>
        )}
        {org.roles.includes("creator") && (
          <Link className="card" to="../creator/drafts">
            <span className="step">02 / CREATE</span>
            <h2>Make your next draft</h2>
            <p>Keep your working copy separate from submitted revisions.</p>
          </Link>
        )}
        {org.roles.includes("reviewer") && (
          <Link className="card" to="../reviewer/inbox">
            <span className="step">03 / REVIEW</span>
            <h2>Review with context</h2>
            <p>Inspect evidence, ask questions, and make the final call.</p>
          </Link>
        )}
      </div>
      <section className="notice">
        <strong>Text review, human judgement.</strong>
        <p>
          VeriLoom checks text against approved organisation sources. Findings
          are suggestions; approval belongs to your assigned reviewer.
        </p>
      </section>
    </>
  );
}
function Members() {
  const org = useOrg();
  const [token, setToken] = useState("");
  const members = usePages<{
    user_id: string;
    display_name: string;
    email: string;
    roles: string[];
  }>(`/organisations/${org.org_id}/members`);
  if (!org.roles.includes("admin")) return <h1>Access denied</h1>;
  return (
    <>
      <h1>People & invitations</h1>
      <ErrorNotice error={members.error} />
      {members.data?.pages
        .flatMap((p) => p.items)
        .map((m) => (
          <article className="card" key={m.user_id}>
            <strong>{m.display_name}</strong>
            <p>
              {m.email} · {m.roles.join(", ")}
            </p>
            <ActionForm
              label="Update roles"
              onSubmit={async (f) => {
                const roles = f.getAll("roles");
                if (!roles.length) throw new Error("Choose at least one role.");
                await api(
                  `/organisations/${org.org_id}/members/${m.user_id}`,
                  "PATCH",
                  { roles },
                );
              }}
            >
              <fieldset>
                <legend>Roles for {m.display_name}</legend>
                {["admin", "creator", "reviewer"].map((role) => (
                  <label className="role-option" key={role}>
                    <input
                      type="checkbox"
                      name="roles"
                      value={role}
                      defaultChecked={m.roles.includes(role)}
                    />
                    {role}
                  </label>
                ))}
              </fieldset>
            </ActionForm>
          </article>
        ))}
      <More {...members} />
      <section className="card">
        <h2>Invite a teammate</h2>
        <p>Share the returned token manually with the invited email address.</p>
        <ActionForm
          label="Create invitation"
          onSubmit={async (f) => {
            const r = await api<{
              invitation_token: string;
              expires_at: string;
            }>(`/organisations/${org.org_id}/invitations`, "POST", {
              email: f.get("email"),
              role: f.get("role"),
              expires_in_hours: Number(f.get("expires_in_hours")),
            });
            setToken(`Token: ${r.invitation_token}\nExpires: ${r.expires_at}`);
          }}
        >
          <Field label="Email">
            <input type="email" required name="email" />
          </Field>
          <Field label="Role">
            <select name="role">
              <option value="creator">Creator</option>
              <option value="reviewer">Reviewer</option>
            </select>
          </Field>
          <Field label="Expires in hours">
            <input
              type="number"
              name="expires_in_hours"
              min={1}
              max={168}
              defaultValue={24}
            />
          </Field>
        </ActionForm>
        <pre role="status">{token}</pre>
      </section>
    </>
  );
}
export function App() {
  return (
    <Routes>
      <Route path="/login" element={<Auth />} />
      <Route path="/register" element={<Auth register />} />
      <Route element={<RequireSession />}>
        <Route path="/organisations" element={<Organisations />} />
        <Route path="/invitations/accept" element={<Organisations />} />
        <Route path="/org/:orgId" element={<Shell />}>
          <Route path="overview" element={<Overview />} />
          <Route path="admin/sources" element={<Sources />} />
          <Route path="admin/members" element={<Members />} />
          <Route path="creator/drafts" element={<DraftList />} />
          <Route path="creator/drafts/new" element={<DraftEditor />} />
          <Route path="creator/drafts/:draftId" element={<DraftEditor />} />
          <Route path="reviewer/inbox" element={<DraftList assigned />} />
          <Route path="reviewer/inbox/:reviewId" element={<AssignedDraft />} />
          <Route
            path="reviewer/reviews/:reviewId"
            element={<ReviewWorkspace />}
          />
        </Route>
        <Route path="/" element={<Navigate to="/organisations" replace />} />
      </Route>
      <Route
        path="*"
        element={
          <main>
            <h1>Page not found</h1>
            <Link to="/">Return to workspace</Link>
          </main>
        }
      />
    </Routes>
  );
}
