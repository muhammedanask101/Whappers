"use client";

import { useEffect, useMemo, useState } from "react";

const apiBase = process.env.NEXT_PUBLIC_API_BASE_URL || "http://localhost:8000";

async function apiFetch(path, token, options = {}) {
  const response = await fetch(`${apiBase}${path}`, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      "x-admin-token": token,
      ...(options.headers || {})
    }
  });

  if (!response.ok) {
    throw new Error(`API request failed: ${response.status}`);
  }

  return response.json();
}

export default function DashboardPage() {
  const [token, setToken] = useState("");
  const [savedToken, setSavedToken] = useState("");

  const [businesses, setBusinesses] = useState([]);
  const [selectedBusiness, setSelectedBusiness] = useState("");

  const [dashboard, setDashboard] = useState(null);
  const [orders, setOrders] = useState([]);

  useEffect(() => {
    const stored = window.localStorage.getItem("admin_token") || "";
    setToken(stored);
    setSavedToken(stored);
  }, []);

  // Load businesses
  useEffect(() => {
    if (!savedToken) return;

    apiFetch("/admin/businesses", savedToken)
      .then((biz) => {
        setBusinesses(biz);
        if (!selectedBusiness && biz[0]) {
          setSelectedBusiness(biz[0].business_id);
        }
      })
      .catch(() => setBusinesses([]));
  }, [savedToken]);

  // Load dashboard data
  useEffect(() => {
    if (!savedToken || !selectedBusiness) return;

    Promise.all([
      apiFetch(`/admin/dashboard/${selectedBusiness}`, savedToken),
      apiFetch("/admin/orders", savedToken)
    ])
      .then(([dash, ord]) => {
        setDashboard(dash);
        setOrders(ord);
      })
      .catch(() => {
        setDashboard(null);
        setOrders([]);
      });
  }, [savedToken, selectedBusiness]);

  const selectedOrders = useMemo(
    () => orders.filter((o) => o.business_id === selectedBusiness),
    [orders, selectedBusiness]
  );

  function handleLogin(e) {
    e.preventDefault();
    window.localStorage.setItem("admin_token", token);
    setSavedToken(token);
  }

  if (!savedToken) {
    return (
      <main style={styles.center}>
        <form onSubmit={handleLogin} style={styles.card}>
          <h1>Super Admin</h1>
          <input
            value={token}
            onChange={(e) => setToken(e.target.value)}
            placeholder="Admin token"
            style={styles.input}
          />
          <button style={styles.button}>Continue</button>
        </form>
      </main>
    );
  }

  return (
    <main style={styles.shell}>
      {/* Sidebar */}
      <section style={styles.sidebar}>
        <h2>Businesses</h2>

        {businesses.map((b) => (
          <button
            key={b.business_id}
            onClick={() => setSelectedBusiness(b.business_id)}
            style={{
              ...styles.businessButton,
              borderColor:
                selectedBusiness === b.business_id ? "#000" : "#ddd"
            }}
          >
            <strong>{b.name}</strong>
            <span>{b.whatsapp_number}</span>
          </button>
        ))}
      </section>

      {/* Main */}
      <section style={styles.main}>
        <h1>AI Sales Dashboard</h1>

        {/* Metrics */}
        <div style={styles.metrics}>
          <Metric label="Revenue" value={dashboard?.metrics?.total_revenue ?? 0} />
          <Metric label="Orders" value={dashboard?.metrics?.total_orders ?? 0} />
          <Metric label="Customers" value={dashboard?.metrics?.total_customers ?? 0} />
          <Metric label="Avg Order" value={dashboard?.metrics?.avg_order_value ?? 0} />
        </div>

        {/* Insights */}
        <div style={styles.panel}>
          <h2>🧠 AI Insights</h2>
          {dashboard?.insights?.length === 0 && <p>No insights yet</p>}
          {dashboard?.insights?.map((i, idx) => (
            <div key={idx} style={styles.insight}>
              {i.message}
            </div>
          ))}
        </div>

        {/* Top Products */}
        <div style={styles.panel}>
          <h2>🔥 Top Products</h2>
          {dashboard?.top_products?.map((p, idx) => (
            <div key={idx} style={styles.row}>
              <span>{p.name}</span>
              <strong>{p.quantity}</strong>
            </div>
          ))}
        </div>

        {/* Orders */}
        <div style={styles.panel}>
          <h2>Orders</h2>
          <Table
            rows={selectedOrders}
            columns={["order_id", "customer_phone", "status", "total"]}
          />
        </div>
      </section>
    </main>
  );
}

function Metric({ label, value }) {
  return (
    <div style={styles.metric}>
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}

function Table({ rows, columns }) {
  return (
    <table style={{ width: "100%" }}>
      <thead>
        <tr>
          {columns.map((c) => (
            <th key={c}>{c}</th>
          ))}
        </tr>
      </thead>
      <tbody>
        {rows.map((r, i) => (
          <tr key={i}>
            {columns.map((c) => (
              <td key={c}>{r[c]}</td>
            ))}
          </tr>
        ))}
      </tbody>
    </table>
  );
}

const styles = {
  center: { minHeight: "100vh", display: "grid", placeItems: "center" },
  shell: { display: "grid", gridTemplateColumns: "300px 1fr", minHeight: "100vh" },
  sidebar: { padding: 20, borderRight: "1px solid #ddd" },
  main: { padding: 20, display: "grid", gap: 20 },
  card: { padding: 20, border: "1px solid #ddd", borderRadius: 10 },
  metrics: { display: "grid", gridTemplateColumns: "repeat(4,1fr)", gap: 10 },
  metric: { padding: 15, border: "1px solid #ddd", borderRadius: 10 },
  panel: { padding: 20, border: "1px solid #ddd", borderRadius: 10 },
  input: { padding: 10, border: "1px solid #ccc", borderRadius: 8 },
  button: { padding: 10, background: "#111", color: "#fff", borderRadius: 8 },
  businessButton: { padding: 10, marginBottom: 10, border: "1px solid #ddd", borderRadius: 8 },
  insight: { padding: 10, background: "#fff3cd", marginBottom: 8, borderRadius: 6 },
  row: { display: "flex", justifyContent: "space-between", padding: 8 }
};