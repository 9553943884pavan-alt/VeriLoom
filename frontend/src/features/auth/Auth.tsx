import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import { Link, useNavigate } from "react-router-dom";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "../../lib/api/client";
import { ErrorNotice, Field } from "../../components/ui";
const schema = z.object({
  email: z.email(),
  password: z.string().min(1).max(128),
  display_name: z.string().max(120),
});
type Values = z.infer<typeof schema>;
export function Auth({ register = false }: { register?: boolean }) {
  const navigate = useNavigate();
  const client = useQueryClient();
  const form = useForm<Values>({
    resolver: zodResolver(schema),
    defaultValues: { email: "", password: "", display_name: "" },
  });
  const action = useMutation({
    mutationFn: async (v: Values) => {
      if (register && (v.password.length < 12 || !v.display_name.trim()))
        throw new Error(
          "Use a display name and a password of at least 12 characters.",
        );
      return api(
        `/auth/${register ? "register" : "login"}`,
        "POST",
        register ? v : { email: v.email, password: v.password },
      );
    },
    onSuccess: async () => {
      await client.invalidateQueries({ queryKey: ["session"] });
      navigate(register ? "/login?registered=1" : "/organisations");
    },
  });
  return (
    <main className="auth-page">
      <div className="auth-shell">
        <section className="auth-form-pane">
          <Link className="brand" to="/">
            <span className="brand-mark" aria-hidden="true">◈</span>
            VeriLoom
          </Link>
          <div className="auth-intro">
            <p className="eyebrow">Grounded guidance · Human decisions</p>
            <h1>{register ? "Create your account" : "Welcome back"}</h1>
            <p>Bring every draft, source, and review together in one clear workspace.</p>
          </div>
          <form className="auth-form" onSubmit={form.handleSubmit((v) => action.mutate(v))}>
            {register && (
              <Field label="Display name">
                <input
                  required
                  autoComplete="name"
                  placeholder="Your full name"
                  {...form.register("display_name")}
                />
              </Field>
            )}
            <Field label="Email">
              <input
                type="email"
                required
                autoComplete="email"
                placeholder="you@organisation.com"
                {...form.register("email")}
              />
            </Field>
            <Field label="Password">
              <input
                type="password"
                required
                minLength={register ? 12 : 1}
                autoComplete={register ? "new-password" : "current-password"}
                placeholder={register ? "At least 12 characters" : "Enter your password"}
                {...form.register("password")}
              />
            </Field>
            {Object.values(form.formState.errors).map((e, i) => (
              <p role="alert" className="error" key={i}>
                {e.message}
              </p>
            ))}
            <ErrorNotice error={action.error} />
            <button className="auth-submit" disabled={action.isPending}>
              {action.isPending
                ? "Please wait…"
                : register
                  ? "Create account"
                  : "Sign in"}
            </button>
          </form>
          <p className="auth-switch">
            {register ? "Already registered?" : "New to VeriLoom?"}{" "}
            <Link to={register ? "/login" : "/register"}>
              {register ? "Sign in" : "Create an account"}
            </Link>
          </p>
          {register && (
            <p className="auth-note">Registration creates your account. Sign in afterwards to join your organisation.</p>
          )}
          {!register && new URLSearchParams(location.search).has("registered") && (
            <p role="status" className="auth-note">Account created. You can now sign in.</p>
          )}
        </section>
        <aside className="auth-story" aria-label="How VeriLoom works">
          <div className="auth-story-orbit" aria-hidden="true" />
          <p className="eyebrow">One connected workflow</p>
          <h2>From brand knowledge to confident approval.</h2>
          <p>Keep source guidance, submitted words, AI findings, and human decisions connected.</p>
          <ol className="auth-steps">
            <li><span>01</span><div><strong>Approve sources</strong><small>Make trusted guidance available to reviews.</small></div></li>
            <li><span>02</span><div><strong>Submit drafts</strong><small>Keep working copies and revisions distinct.</small></div></li>
            <li><span>03</span><div><strong>Explore findings</strong><small>Ask follow-up questions with cited context.</small></div></li>
            <li><span>04</span><div><strong>Decide together</strong><small>A human reviewer makes the final call.</small></div></li>
          </ol>
        </aside>
      </div>
    </main>
  );
}
