"use client";

import { useEffect } from "react";
import { usePathname, useRouter } from "next/navigation";

import { useAuthStore } from "@/lib/auth-store";

export default function AuthGuard({ children }) {
  const router = useRouter();
  const pathname = usePathname();
  const hydrated = useAuthStore((state) => state.hydrated);
  const bootstrapped = useAuthStore((state) => state.bootstrapped);
  const user = useAuthStore((state) => state.user);
  const restore = useAuthStore((state) => state.restore);

  useEffect(() => {
    if (!hydrated) return;
    restore();
  }, [hydrated, restore]);

  useEffect(() => {
    if (!hydrated || !bootstrapped) return;
    if (!user && pathname !== "/login" && pathname !== "/signup") {
      router.replace("/login");
    }
  }, [bootstrapped, hydrated, pathname, router, user]);

  if (!hydrated || !bootstrapped) {
    return <div className="grid min-h-screen place-items-center bg-sand text-sm text-ink/70">Restoring session...</div>;
  }

  return children;
}
