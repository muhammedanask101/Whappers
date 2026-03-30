"use client";

import { useQuery } from "@tanstack/react-query";
import { useState } from "react";

import { DataTable, PageHeader, Panel } from "@/components/ui";
import { apiRequest } from "@/lib/api";
import { useAuthStore } from "@/lib/auth-store";

export default function ActivityPage() {
  const { accessToken, user } = useAuthStore();
  const [filters, setFilters] = useState({ business_id: "", phone: "" });

  const query = useQuery({
    queryKey: ["activity", filters],
    queryFn: () =>
      apiRequest(`/api/admin/activity?business_id=${encodeURIComponent(filters.business_id)}&phone=${encodeURIComponent(filters.phone)}`, {
        token: accessToken
      }),
    enabled: user?.role === "superadmin" && !!accessToken && !!filters.business_id
  });

  if (user?.role !== "superadmin") {
    return <Panel title="Restricted">Activity inspection is available to superadmins only.</Panel>;
  }

  const data = query.data || {};

  return (
    <>
      <PageHeader
        eyebrow="Logs and activity"
        title="Operational trace inspection"
        description="Inspect agent traces, interaction events, and automation runs for a specific tenant or phone number."
      />
      <Panel title="Filters">
        <div className="grid gap-4 md:grid-cols-2">
          <input placeholder="Business ID" value={filters.business_id} onChange={(e) => setFilters((state) => ({ ...state, business_id: e.target.value }))} />
          <input placeholder="Phone (optional)" value={filters.phone} onChange={(e) => setFilters((state) => ({ ...state, phone: e.target.value }))} />
        </div>
      </Panel>
      <div className="grid gap-6">
        <Panel title="Agent traces">
          <DataTable
            columns={[
              { key: "phone", label: "Phone" },
              { key: "role", label: "Role" },
              { key: "message", label: "Message" },
              { key: "created_at", label: "Created" }
            ]}
            rows={data.agent_traces || []}
          />
        </Panel>
        <Panel title="Interaction events">
          <DataTable
            columns={[
              { key: "kind", label: "Kind" },
              { key: "phone", label: "Phone" },
              { key: "created_at", label: "Created" },
              { key: "payload", label: "Payload", render: (row) => JSON.stringify(row.payload || {}) }
            ]}
            rows={data.events || []}
          />
        </Panel>
      </div>
    </>
  );
}
