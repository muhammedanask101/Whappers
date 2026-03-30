"use client";

import { useQuery } from "@tanstack/react-query";

import { DataTable, PageHeader, Panel } from "@/components/ui";
import { apiRequest } from "@/lib/api";
import { useAuthStore } from "@/lib/auth-store";

export default function VendorsPage() {
  const { accessToken, user } = useAuthStore();
  const isSuperadmin = user?.role === "superadmin";
  const path = isSuperadmin ? "/api/admin/vendors" : "/api/business/vendors";
  const query = useQuery({
    queryKey: ["vendors", isSuperadmin, user?.business_id],
    queryFn: () => apiRequest(path, { token: accessToken }),
    enabled: !!accessToken && !!user
  });

  return (
    <>
      <PageHeader
        eyebrow="Vendors"
        title="Vendor operations"
        description="Track assigned vendor phones, queue depth, open support, and the most recent operational activity."
      />
      <Panel title="Vendor activity">
        <DataTable
          columns={[
            { key: "business_name", label: "Business" },
            { key: "vendor_phone", label: "Vendor phone" },
            { key: "whatsapp_number", label: "WhatsApp" },
            { key: "orders_total", label: "Orders", render: (row) => row.activity?.orders_total || 0 },
            { key: "orders_pending", label: "Pending", render: (row) => row.activity?.orders_pending || 0 },
            { key: "last_order_at", label: "Last order", render: (row) => row.activity?.last_order_at || "-" }
          ]}
          rows={query.data || []}
        />
      </Panel>
    </>
  );
}
