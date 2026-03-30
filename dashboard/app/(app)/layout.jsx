"use client";

import AuthGuard from "@/components/auth-guard";
import AppShell from "@/components/app-shell";

export default function DashboardLayout({ children }) {
  return (
    <AuthGuard>
      <AppShell>{children}</AppShell>
    </AuthGuard>
  );
}
