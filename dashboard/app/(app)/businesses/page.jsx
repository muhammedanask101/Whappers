"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { DataTable, PageHeader, Panel } from "@/components/ui";
import { apiRequest } from "@/lib/api";
import { useAuthStore } from "@/lib/auth-store";

const initialForm = { name: "", whatsapp_number: "", vendor_phone: "", business_type: "retail", plan: "basic" };

export default function BusinessesPage() {
  const { accessToken, user } = useAuthStore();
  const queryClient = useQueryClient();
  const [form, setForm] = useState(initialForm);

  const businessesQuery = useQuery({
    queryKey: ["admin-businesses"],
    queryFn: () => apiRequest("/api/admin/businesses", { token: accessToken }),
    enabled: user?.role === "superadmin" && !!accessToken
  });

  const createMutation = useMutation({
    mutationFn: (payload) => apiRequest("/api/admin/businesses", { token: accessToken, method: "POST", body: payload }),
    onSuccess: () => {
      setForm(initialForm);
      queryClient.invalidateQueries({ queryKey: ["admin-businesses"] });
    }
  });

  if (user?.role !== "superadmin") {
    return <Panel title="Restricted">This view is available to superadmins only.</Panel>;
  }

  return (
    <>
      <PageHeader
        eyebrow="Businesses"
        title="Tenant directory"
        description="Create, inspect, and tune business tenants, assign WhatsApp numbers, and monitor onboarding and subscription status."
      />
      <div className="grid gap-6 xl:grid-cols-[1.1fr_0.9fr]">
        <Panel title="All businesses" subtitle="Live tenant inventory with onboarding and subscription context.">
          <DataTable
            columns={[
              { key: "name", label: "Business" },
              { key: "whatsapp_number", label: "WhatsApp" },
              { key: "vendor_phone", label: "Vendor" },
              { key: "plan", label: "Plan", render: (row) => row.subscription?.plan || "basic" },
              { key: "orders", label: "Orders", render: (row) => row.summary?.order_count || 0 },
              { key: "onboarding", label: "Onboarding", render: (row) => row.onboarding?.step || "signup" }
            ]}
            rows={businessesQuery.data || []}
          />
        </Panel>
        <Panel title="Create business" subtitle="Provision a new tenant without leaving the dashboard.">
          <form
            className="grid gap-4"
            onSubmit={(event) => {
              event.preventDefault();
              createMutation.mutate(form);
            }}
          >
            <input placeholder="Business name" value={form.name} onChange={(e) => setForm((state) => ({ ...state, name: e.target.value }))} required />
            <input placeholder="WhatsApp number" value={form.whatsapp_number} onChange={(e) => setForm((state) => ({ ...state, whatsapp_number: e.target.value }))} required />
            <input placeholder="Vendor phone" value={form.vendor_phone} onChange={(e) => setForm((state) => ({ ...state, vendor_phone: e.target.value }))} required />
            <select value={form.business_type} onChange={(e) => setForm((state) => ({ ...state, business_type: e.target.value }))}>
              <option value="retail">Retail</option>
              <option value="restaurant">Restaurant</option>
              <option value="pharmacy">Pharmacy</option>
              <option value="services">Services</option>
            </select>
            <select value={form.plan} onChange={(e) => setForm((state) => ({ ...state, plan: e.target.value }))}>
              <option value="basic">Basic</option>
              <option value="pro">Pro</option>
              <option value="enterprise">Enterprise</option>
            </select>
            <button className="rounded-[20px] bg-ink px-4 py-3 text-sm font-semibold text-white" disabled={createMutation.isPending} type="submit">
              {createMutation.isPending ? "Creating..." : "Create tenant"}
            </button>
          </form>
        </Panel>
      </div>
    </>
  );
}
