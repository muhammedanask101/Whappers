"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { PageHeader, Panel } from "@/components/ui";
import { apiRequest } from "@/lib/api";
import { useAuthStore } from "@/lib/auth-store";

const steps = [
  ["signup", "Account created"],
  ["business_info", "Business details added"],
  ["catalog", "Catalog uploaded"],
  ["whatsapp_number", "WhatsApp number assigned"],
  ["activate", "Tenant activated"]
];

export default function OnboardingPage() {
  const { accessToken, user } = useAuthStore();
  const queryClient = useQueryClient();
  const [catalogItem, setCatalogItem] = useState({ name: "", price: "", unit: "unit", category: "" });

  const onboardingQuery = useQuery({
    queryKey: ["onboarding", user?.business_id],
    queryFn: () => apiRequest("/api/onboarding/status", { token: accessToken }),
    enabled: !!accessToken && !!user && user.role !== "superadmin"
  });

  const stepMutation = useMutation({
    mutationFn: (payload) => apiRequest("/api/onboarding/step", { token: accessToken, method: "POST", body: payload }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["onboarding", user?.business_id] })
  });

  const catalogMutation = useMutation({
    mutationFn: (payload) => apiRequest("/api/business/catalog", { token: accessToken, method: "POST", body: payload }),
    onSuccess: () => {
      setCatalogItem({ name: "", price: "", unit: "unit", category: "" });
      queryClient.invalidateQueries({ queryKey: ["onboarding", user?.business_id] });
    }
  });

  if (user?.role === "superadmin") {
    return <Panel title="Restricted">Business onboarding is a tenant-scoped workflow.</Panel>;
  }

  const onboarding = onboardingQuery.data || {};

  return (
    <>
      <PageHeader eyebrow="Onboarding" title="Activation workflow" description="Complete the business rollout from signup through WhatsApp assignment and activation." />
      <div className="grid gap-6 xl:grid-cols-[0.8fr_1.2fr]">
        <Panel title="Progress">
          <div className="space-y-3">
            {steps.map(([step, label]) => {
              const complete = onboarding.completed_steps?.includes(step) || onboarding.is_complete;
              return (
                <button
                  key={step}
                  onClick={() => stepMutation.mutate({ step, mark_complete: step === "activate" })}
                  className={`flex w-full items-center justify-between rounded-2xl px-4 py-4 text-left ${complete ? "bg-ink text-white" : "bg-sand/70 text-ink"}`}
                >
                  <span>{label}</span>
                  <span className="text-xs uppercase tracking-[0.22em]">{complete ? "Done" : "Mark"}</span>
                </button>
              );
            })}
          </div>
        </Panel>
        <Panel title="Catalog quick upload" subtitle="Seed the catalog as part of onboarding.">
          <form
            className="grid gap-4 md:grid-cols-2"
            onSubmit={(event) => {
              event.preventDefault();
              catalogMutation.mutate({ ...catalogItem, price: Number(catalogItem.price) });
            }}
          >
            <input placeholder="Product name" value={catalogItem.name} onChange={(e) => setCatalogItem((state) => ({ ...state, name: e.target.value }))} required />
            <input placeholder="Price" value={catalogItem.price} onChange={(e) => setCatalogItem((state) => ({ ...state, price: e.target.value }))} required />
            <input placeholder="Unit" value={catalogItem.unit} onChange={(e) => setCatalogItem((state) => ({ ...state, unit: e.target.value }))} />
            <input placeholder="Category" value={catalogItem.category} onChange={(e) => setCatalogItem((state) => ({ ...state, category: e.target.value }))} />
            <div className="md:col-span-2">
              <button className="rounded-[20px] bg-ink px-4 py-3 text-sm font-semibold text-white" disabled={catalogMutation.isPending} type="submit">
                {catalogMutation.isPending ? "Uploading..." : "Add catalog item"}
              </button>
            </div>
          </form>
        </Panel>
      </div>
    </>
  );
}
