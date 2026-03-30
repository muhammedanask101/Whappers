"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { PageHeader, Panel } from "@/components/ui";
import { apiRequest } from "@/lib/api";
import { useAuthStore } from "@/lib/auth-store";

export default function SettingsPage() {
  const { accessToken, user } = useAuthStore();
  const queryClient = useQueryClient();
  const isSuperadmin = user?.role === "superadmin";
  const query = useQuery({
    queryKey: ["settings", user?.business_id],
    queryFn: () => apiRequest("/api/business/settings", { token: accessToken }),
    enabled: !!accessToken && !!user && !isSuperadmin
  });
  const [form, setForm] = useState({});

  const mutation = useMutation({
    mutationFn: (payload) => apiRequest("/api/business/settings", { token: accessToken, method: "PUT", body: payload }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["settings", user?.business_id] })
  });

  const settings = query.data || {};
  const source = Object.keys(form).length ? form : settings;

  if (isSuperadmin) {
    return (
      <>
        <PageHeader eyebrow="Settings" title="Platform security model" description="Superadmin access is scoped through APIs, not a server-rendered backend console." />
        <Panel title="Current mode">
          <p className="text-sm text-ink/65">Use the Businesses and Subscriptions flows to manage tenant configuration. Backend UI routes have been removed.</p>
        </Panel>
      </>
    );
  }

  return (
    <>
      <PageHeader eyebrow="Settings" title="Business configuration" description="Edit the operational profile consumed by WhatsApp flows and dashboard APIs." />
      <Panel title="Editable settings">
        <form
          className="grid gap-4 md:grid-cols-2"
          onSubmit={(event) => {
            event.preventDefault();
            mutation.mutate(form);
          }}
        >
          {[
            ["name", "Business name"],
            ["contact_phone", "Contact phone"],
            ["address", "Address"],
            ["hours", "Hours"],
            ["support_details", "Support details"],
            ["delivery_info", "Delivery info"]
          ].map(([key, label]) => (
            <div key={key}>
              <label className="mb-2 block text-sm text-ink/60">{label}</label>
              <input value={source[key] || ""} onChange={(e) => setForm((state) => ({ ...state, [key]: e.target.value }))} />
            </div>
          ))}
          <div className="md:col-span-2">
            <button className="rounded-[20px] bg-ink px-4 py-3 text-sm font-semibold text-white" disabled={mutation.isPending} type="submit">
              {mutation.isPending ? "Saving..." : "Save settings"}
            </button>
          </div>
        </form>
      </Panel>
    </>
  );
}
