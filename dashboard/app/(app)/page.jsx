"use client";

import { useQuery } from "@tanstack/react-query";

import { PageHeader, Panel, ProductChart, StatCard, TrendChart } from "@/components/ui";
import { apiRequest } from "@/lib/api";
import { useAuthStore } from "@/lib/auth-store";

export default function OverviewPage() {
  const { accessToken, user } = useAuthStore();
  const isSuperadmin = user?.role === "superadmin";
  const overviewQuery = useQuery({
    queryKey: ["overview", isSuperadmin, user?.business_id],
    queryFn: () => apiRequest(isSuperadmin ? "/api/admin/overview" : "/api/business/me", { token: accessToken }),
    enabled: !!accessToken && !!user
  });
  const analyticsQuery = useQuery({
    queryKey: ["analytics", isSuperadmin, user?.business_id],
    queryFn: () =>
      apiRequest(isSuperadmin ? "/api/admin/analytics" : "/api/business/performance", {
        token: accessToken
      }),
    enabled: !!accessToken && !!user
  });

  const overview = overviewQuery.data || {};
  const analytics = analyticsQuery.data || {};
  const businessSummary = overview.summary || {};
  const metrics = analytics.metrics || {};

  return (
    <>
      <PageHeader
        eyebrow={isSuperadmin ? "Platform overview" : "Business overview"}
        title={isSuperadmin ? "Tenant-wide operational pulse" : overview.name || "Business dashboard"}
        description={
          isSuperadmin
            ? "Track revenue, businesses, order throughput, and platform-wide product velocity without touching backend UI."
            : "Monitor your store performance, onboarding completion, catalog readiness, and subscription-backed capabilities."
        }
      />
      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
        <StatCard label="Revenue" value={`Rs ${isSuperadmin ? overview.revenue || 0 : metrics.total_revenue || 0}`} />
        <StatCard label="Orders" value={isSuperadmin ? overview.orders || 0 : metrics.total_orders || 0} />
        <StatCard label={isSuperadmin ? "Businesses" : "Products"} value={isSuperadmin ? overview.businesses || 0 : businessSummary.product_count || 0} />
        <StatCard label={isSuperadmin ? "Open support" : "Subscription"} value={isSuperadmin ? overview.support_open || 0 : overview.subscription?.plan || "basic"} />
      </div>
      <div className="grid gap-6 xl:grid-cols-[1.2fr_0.8fr]">
        <Panel title="Order trends" subtitle="Revenue and order movement across the latest activity window.">
          <TrendChart data={analytics.order_trends || []} />
        </Panel>
        <Panel title="Top products" subtitle="Most ordered items by quantity.">
          <ProductChart data={(isSuperadmin ? overview.top_products : analytics.top_products) || []} />
        </Panel>
      </div>
      <Panel title="AI insights" subtitle="Generated operational signals from tenant activity.">
        <div className="grid gap-3">
          {(analytics.insights || []).map((item, index) => (
            <div key={index} className="rounded-2xl bg-sand/70 px-4 py-4 text-sm text-ink/75">
              {item.message || JSON.stringify(item)}
            </div>
          ))}
          {!analytics.insights?.length ? <div className="rounded-2xl bg-sand/70 px-4 py-4 text-sm text-ink/65">No generated insights yet.</div> : null}
        </div>
      </Panel>
    </>
  );
}
