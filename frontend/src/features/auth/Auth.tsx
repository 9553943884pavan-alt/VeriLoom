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
    <main className="auth">
      <Link className="brand" to="/">
        ◈ VeriLoom
      </Link>
      <p className="eyebrow">Grounded guidance. Human decisions.</p>
      <h1>{register ? "Create your account" : "Welcome back"}</h1>
      <p>Review your words against approved brand knowledge.</p>
      <form onSubmit={form.handleSubmit((v) => action.mutate(v))}>
        {register && (
          <Field label="Display name">
            <input
              required
              autoComplete="name"
              {...form.register("display_name")}
            />
          </Field>
        )}
        <Field label="Email">
          <input
            type="email"
            required
            autoComplete="email"
            {...form.register("email")}
          />
        </Field>
        <Field label="Password">
          <input
            type="password"
            required
            minLength={register ? 12 : 1}
            autoComplete={register ? "new-password" : "current-password"}
            {...form.register("password")}
          />
        </Field>
        {Object.values(form.formState.errors).map((e, i) => (
          <p role="alert" className="error" key={i}>
            {e.message}
          </p>
        ))}
        <ErrorNotice error={action.error} />
        <button disabled={action.isPending}>
          {action.isPending
            ? "Please wait…"
            : register
              ? "Create account"
              : "Sign in"}
        </button>
      </form>
      <p>
        {register ? "Already registered?" : "New to VeriLoom?"}{" "}
        <Link to={register ? "/login" : "/register"}>
          {register ? "Sign in" : "Create an account"}
        </Link>
      </p>
      {register && (
        <small>
          Registration creates your account. Sign in afterwards to join your
          organisation.
        </small>
      )}
      {!register && new URLSearchParams(location.search).has("registered") && (
        <p role="status">Account created. You can now sign in.</p>
      )}
    </main>
  );
}
