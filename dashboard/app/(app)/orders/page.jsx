"use client";

import { useQuery } from "@tanstack/react-query";
import { useState } from "react";

import { DataTable, PageHeader, Panel } from "@/components/ui";
import { apiRequest } from "@/lib/api";
import { useAuthStore } from "@/lib/auth-store";

export default function OrdersPage() {
  const { accessToken, user } = useAuthStore();
  const [filters, setFilters] = useState({ business_id: "", status: "", date_from: "", date_to: "" });
  const isSuperadmin = user?.role === "superadmin";

  const path = isSuperadmin
    ? `/api/admin/orders?business_id=${encodeURIComponent(filters.business_id)}&status=${encodeURIComponent(filters.status)}&date_from=${encodeURIComponent(filters.date_from)}&date_to=${encodeURIComponent(filters.date_to)}`
    : `/api/business/orders?status=${encodeURIComponent(filters.status)}&date_from=${encodeURIComponent(filters.date_from)}&date_to=${encodeURIComponent(filters.date_to)}`;

  const ordersQuery = useQuery({
    queryKey: ["orders", user?.role, filters],
    queryFn: () => apiRequest(path, { token: accessToken }),
    enabled: !!accessToken && !!user
  });

  return (
    <>
      <PageHeader
        eyebrow="Orders"
        title={isSuperadmin ? "Cross-tenant order stream" : "Your order queue"}
        description="Filter by business, status, and date while preserving tenant isolation at the API boundary."
      />
      <Panel title="Filters">
        <div className="grid gap-4 md:grid-cols-4">
          {isSuperadmin ? <input placeholder="Business ID" value={filters.business_id} onChange={(e) => setFilters((state) => ({ ...state, business_id: e.target.value }))} /> : null}
          <input placeholder="Status" value={filters.status} onChange={(e) => setFilters((state) => ({ ...state, status: e.target.value }))} />
          <input placeholder="Date from (ISO)" value={filters.date_from} onChange={(e) => setFilters((state) => ({ ...state, date_from: e.target.value }))} />
          <input placeholder="Date to (ISO)" value={filters.date_to} onChange={(e) => setFilters((state) => ({ ...state, date_to: e.target.value }))} />
        </div>
      </Panel>
      <Panel title="Orders" subtitle="Realtime operational list view with secure API-backed filters.">
        <DataTable
          columns={[
            { key: "business_id", label: "Business" },
            { key: "order_id", label: "Order" },
            { key: "customer_phone", label: "Customer" },
            { key: "status", label: "Status" },
            { key: "total", label: "Total", render: (row) => `Rs ${row.total}` },
            { key: "created_at", label: "Created" }
          ]}
          rows={ordersQuery.data || []}
        />
      </Panel>
    </>
  );
}
