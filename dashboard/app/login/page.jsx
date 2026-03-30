"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";

import { useAuthStore } from "@/lib/auth-store";

export default function LoginPage() {
  const router = useRouter();
  const login = useAuthStore((state) => state.login);
  const [mode, setMode] = useState("credentials");
  const [form, setForm] = useState({ email: "", password: "", admin_token: "" });
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  async function handleSubmit(event) {
    event.preventDefault();
    setLoading(true);
    setError("");
    try {
      if (mode === "token") {
        await login({ admin_token: form.admin_token });
      } else {
        await login({ email: form.email, password: form.password });
      }
      router.replace("/");
    } catch (err) {
      setError(err.message || "Login failed.");
    } finally {
      setLoading(false);
    }
  }

  return (
    <main className="grid min-h-screen place-items-center bg-[radial-gradient(circle_at_top,_rgba(29,79,70,0.22),_transparent_24%),linear-gradient(180deg,_#f7efe4,_#e8f2ed)] px-4">
      <div className="grid w-full max-w-5xl gap-6 lg:grid-cols-[1.15fr_0.85fr]">
        <section className="rounded-[32px] border border-white/70 bg-white/30 p-8 text-ink shadow-panel backdrop-blur">
          <div className="text-xs uppercase tracking-[0.28em] text-ink/45">Production Transition</div>
          <h1 className="mt-4 max-w-xl text-5xl font-semibold leading-tight">Operate every tenant from one dashboard without leaving the API-first architecture.</h1>
          <p className="mt-5 max-w-2xl text-base text-ink/65">
            Superadmins manage businesses, revenue, vendors, and activity globally. Business admins get scoped access to orders,
            catalog, onboarding, and settings through the same secure auth model.
          </p>
          <div className="mt-8 grid gap-4 sm:grid-cols-3">
            {[
              "JWT access + refresh flow",
              "Multi-tenant role enforcement",
              "Onboarding and subscriptions"
            ].map((item) => (
              <div key={item} className="rounded-[24px] border border-white/80 bg-white/65 px-5 py-4 text-sm text-ink/78">
                {item}
              </div>
            ))}
          </div>
        </section>
        <section className="rounded-[32px] border border-white/70 bg-white/80 p-8 shadow-panel">
          <div className="mb-6 flex gap-2 rounded-full bg-sand p-1 text-sm">
            <button
              className={`rounded-full px-4 py-2 ${mode === "credentials" ? "bg-ink text-white" : "text-ink/65"}`}
              onClick={() => setMode("credentials")}
              type="button"
            >
              Email login
            </button>
            <button
              className={`rounded-full px-4 py-2 ${mode === "token" ? "bg-ink text-white" : "text-ink/65"}`}
              onClick={() => setMode("token")}
              type="button"
            >
              Legacy token
            </button>
          </div>
          <form className="space-y-4" onSubmit={handleSubmit}>
            {mode === "credentials" ? (
              <>
                <div>
                  <label className="mb-2 block text-sm text-ink/60">Email</label>
                  <input value={form.email} onChange={(e) => setForm((state) => ({ ...state, email: e.target.value }))} required />
                </div>
                <div>
                  <label className="mb-2 block text-sm text-ink/60">Password</label>
                  <input type="password" value={form.password} onChange={(e) => setForm((state) => ({ ...state, password: e.target.value }))} required />
                </div>
              </>
            ) : (
              <div>
                <label className="mb-2 block text-sm text-ink/60">Admin token</label>
                <input value={form.admin_token} onChange={(e) => setForm((state) => ({ ...state, admin_token: e.target.value }))} required />
              </div>
            )}
            {error ? <p className="rounded-2xl bg-[#fff3ef] px-4 py-3 text-sm text-ember">{error}</p> : null}
            <button className="w-full rounded-[20px] bg-ink px-4 py-3 text-sm font-semibold text-white" disabled={loading} type="submit">
              {loading ? "Signing in..." : "Continue"}
            </button>
          </form>
          <p className="mt-5 text-sm text-ink/62">
            New business owner?{" "}
            <Link className="font-semibold text-ember" href="/signup">
              Start onboarding
            </Link>
          </p>
        </section>
      </div>
    </main>
  );
}
