"use client";

import { useQuery } from "@tanstack/react-query";

import { PageHeader, Panel, ProductChart, TrendChart } from "@/components/ui";
import { apiRequest } from "@/lib/api";
import { useAuthStore } from "@/lib/auth-store";

export default function AnalyticsPage() {
  const { accessToken, user } = useAuthStore();
  const isSuperadmin = user?.role === "superadmin";
  const query = useQuery({
    queryKey: ["analytics-page", isSuperadmin, user?.business_id],
    queryFn: () => apiRequest(isSuperadmin ? "/api/admin/analytics" : "/api/business/performance", { token: accessToken }),
    enabled: !!accessToken && !!user
  });

  const data = query.data || {};

  return (
    <>
      <PageHeader
        eyebrow="Analytics"
        title={isSuperadmin ? "Platform analytics" : "Business performance analytics"}
        description="Revenue, top products, and order trend visibility delivered entirely through API contracts."
      />
      <div className="grid gap-6 xl:grid-cols-[1.2fr_0.8fr]">
        <Panel title="Trendline" subtitle="Revenue and order progression.">
          <TrendChart data={data.order_trends || []} />
        </Panel>
        <Panel title="Top products" subtitle="Top sellers by quantity.">
          <ProductChart data={data.top_products || []} />
        </Panel>
      </div>
      <Panel title="Metrics payload">
        <pre className="overflow-auto rounded-2xl bg-dusk px-4 py-4 font-mono text-xs text-white">
          {JSON.stringify(data.metrics || data.overview || {}, null, 2)}
        </pre>
      </Panel>
    </>
  );
}
