"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import clsx from "clsx";

import { useAuthStore } from "@/lib/auth-store";

const navByRole = {
  superadmin: [
    { href: "/", label: "Overview" },
    { href: "/businesses", label: "Businesses" },
    { href: "/orders", label: "Orders" },
    { href: "/analytics", label: "Analytics" },
    { href: "/vendors", label: "Vendors" },
    { href: "/activity", label: "Activity" },
    { href: "/settings", label: "Settings" }
  ],
  business_admin: [
    { href: "/", label: "Overview" },
    { href: "/orders", label: "Orders" },
    { href: "/analytics", label: "Performance" },
    { href: "/onboarding", label: "Onboarding" },
    { href: "/settings", label: "Settings" }
  ]
};

export default function AppShell({ children }) {
  const pathname = usePathname();
  const router = useRouter();
  const user = useAuthStore((state) => state.user);
  const logout = useAuthStore((state) => state.logout);
  const nav = navByRole[user?.role] || navByRole.business_admin;

  async function handleLogout() {
    await logout();
    router.replace("/login");
  }

  return (
    <div className="min-h-screen bg-[radial-gradient(circle_at_top_left,_rgba(175,77,31,0.17),_transparent_22%),radial-gradient(circle_at_top_right,_rgba(29,79,70,0.18),_transparent_24%),linear-gradient(180deg,_#f7efe4,_#edf3ef)] text-ink">
      <div className="mx-auto grid min-h-screen max-w-7xl gap-6 px-4 py-4 lg:grid-cols-[280px_1fr]">
        <aside className="rounded-[28px] border border-white/60 bg-white/70 p-5 shadow-panel backdrop-blur">
          <div className="mb-8">
            <div className="text-xs uppercase tracking-[0.28em] text-ink/45">WhatsApp Commerce OS</div>
            <h1 className="mt-3 text-2xl font-semibold">{user?.role === "superadmin" ? "Superadmin" : "Business Console"}</h1>
            <p className="mt-2 text-sm text-ink/60">{user?.email}</p>
          </div>
          <nav className="space-y-2">
            {nav.map((item) => (
              <Link
                key={item.href}
                href={item.href}
                className={clsx(
                  "flex items-center justify-between rounded-2xl px-4 py-3 text-sm transition",
                  pathname === item.href ? "bg-ink text-white" : "bg-sand/70 text-ink/80 hover:bg-white"
                )}
              >
                <span>{item.label}</span>
              </Link>
            ))}
          </nav>
          <button
            onClick={handleLogout}
            className="mt-8 w-full rounded-2xl border border-ink/15 bg-white px-4 py-3 text-sm font-medium text-ink transition hover:border-ember hover:text-ember"
          >
            Sign out
          </button>
        </aside>
        <main className="space-y-6">{children}</main>
      </div>
    </div>
  );
}
