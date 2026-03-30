"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";

import { signupRequest } from "@/lib/api";
import { useAuthStore } from "@/lib/auth-store";

export default function SignupPage() {
  const router = useRouter();
  const setSession = useAuthStore((state) => state.setSession);
  const [form, setForm] = useState({
    owner_name: "",
    email: "",
    password: "",
    business_name: "",
    business_type: "retail",
    phone: "",
    whatsapp_number: "",
    plan: "basic"
  });
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  async function handleSubmit(event) {
    event.preventDefault();
    setLoading(true);
    setError("");
    try {
      const session = await signupRequest(form);
      setSession(session);
      router.replace("/onboarding");
    } catch (err) {
      setError(err.message || "Signup failed.");
    } finally {
      setLoading(false);
    }
  }

  return (
    <main className="grid min-h-screen place-items-center bg-[linear-gradient(180deg,_#edf4ef,_#f7efe4)] px-4 py-10">
      <div className="w-full max-w-3xl rounded-[32px] border border-white/70 bg-white/85 p-8 shadow-panel">
        <div className="text-xs uppercase tracking-[0.28em] text-ink/45">Business Onboarding</div>
        <h1 className="mt-3 text-4xl font-semibold">Create your tenant workspace</h1>
        <p className="mt-3 max-w-2xl text-sm text-ink/62">Sign up the business owner, provision the tenant, and start the catalog and WhatsApp activation flow.</p>
        <form className="mt-8 grid gap-4 md:grid-cols-2" onSubmit={handleSubmit}>
          {[
            ["owner_name", "Owner name"],
            ["email", "Work email"],
            ["business_name", "Business name"],
            ["phone", "Vendor phone"],
            ["whatsapp_number", "WhatsApp number"]
          ].map(([key, label]) => (
            <div key={key}>
              <label className="mb-2 block text-sm text-ink/60">{label}</label>
              <input value={form[key]} onChange={(e) => setForm((state) => ({ ...state, [key]: e.target.value }))} required={key !== "whatsapp_number"} />
            </div>
          ))}
          <div>
            <label className="mb-2 block text-sm text-ink/60">Password</label>
            <input type="password" value={form.password} onChange={(e) => setForm((state) => ({ ...state, password: e.target.value }))} required />
          </div>
          <div>
            <label className="mb-2 block text-sm text-ink/60">Business type</label>
            <select value={form.business_type} onChange={(e) => setForm((state) => ({ ...state, business_type: e.target.value }))}>
              <option value="retail">Retail</option>
              <option value="restaurant">Restaurant</option>
              <option value="pharmacy">Pharmacy</option>
              <option value="services">Services</option>
            </select>
          </div>
          <div className="md:col-span-2">
            <label className="mb-2 block text-sm text-ink/60">Plan</label>
            <div className="grid gap-3 md:grid-cols-3">
              {[
                ["basic", "Orders, catalog, settings"],
                ["pro", "Adds analytics and performance"],
                ["enterprise", "Adds vendors and activity monitoring"]
              ].map(([plan, description]) => (
                <button
                  key={plan}
                  type="button"
                  onClick={() => setForm((state) => ({ ...state, plan }))}
                  className={`rounded-[24px] border px-4 py-4 text-left ${form.plan === plan ? "border-ink bg-ink text-white" : "border-ink/10 bg-sand/60 text-ink"}`}
                >
                  <div className="text-base font-semibold capitalize">{plan}</div>
                  <div className="mt-1 text-sm opacity-75">{description}</div>
                </button>
              ))}
            </div>
          </div>
          {error ? <p className="md:col-span-2 rounded-2xl bg-[#fff3ef] px-4 py-3 text-sm text-ember">{error}</p> : null}
          <div className="md:col-span-2 flex items-center justify-between gap-4">
            <Link href="/login" className="text-sm font-medium text-ink/65">
              Back to login
            </Link>
            <button className="rounded-[20px] bg-ink px-5 py-3 text-sm font-semibold text-white" disabled={loading} type="submit">
              {loading ? "Creating tenant..." : "Create workspace"}
            </button>
          </div>
        </form>
      </div>
    </main>
  );
}
