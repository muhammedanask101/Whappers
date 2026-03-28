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
  const [orders, setOrders] = useState([]);
  const [usage, setUsage] = useState(null);
  const [support, setSupport] = useState([]);
  const [selectedBusiness, setSelectedBusiness] = useState("");
  const [businessForm, setBusinessForm] = useState({ name: "", whatsapp_number: "", vendor_phone: "", business_type: "retail" });

  useEffect(() => {
    const stored = window.localStorage.getItem("admin_token") || "";
    setToken(stored);
    setSavedToken(stored);
  }, []);

  useEffect(() => {
    if (!savedToken) return;
    Promise.all([
      apiFetch("/admin/businesses", savedToken),
      apiFetch("/admin/orders", savedToken),
      apiFetch("/admin/usage", savedToken),
      apiFetch("/admin/support", savedToken)
    ]).then(([biz, ord, use, sup]) => {
      setBusinesses(biz);
      setOrders(ord);
      setUsage(use);
      setSupport(sup);
      if (!selectedBusiness && biz[0]) setSelectedBusiness(biz[0].business_id);
    }).catch(() => {
      setBusinesses([]);
      setOrders([]);
      setUsage(null);
      setSupport([]);
    });
  }, [savedToken]);

  const selectedOrders = useMemo(
    () => orders.filter((order) => !selectedBusiness || order.business_id === selectedBusiness),
    [orders, selectedBusiness]
  );

  const selectedSupport = useMemo(
    () => support.filter((ticket) => !selectedBusiness || ticket.business_id === selectedBusiness),
    [support, selectedBusiness]
  );

  async function handleLogin(event) {
    event.preventDefault();
    window.localStorage.setItem("admin_token", token);
    setSavedToken(token);
  }

  async function handleCreateBusiness(event) {
    event.preventDefault();
    const created = await apiFetch("/admin/business", savedToken, {
      method: "POST",
      body: JSON.stringify(businessForm)
    });
    setBusinesses((current) => [...current, created]);
    setSelectedBusiness(created.business_id);
    setBusinessForm({ name: "", whatsapp_number: "", vendor_phone: "", business_type: "retail" });
  }

  async function handleDeleteBusiness() {
    if (!selectedBusiness) return;
    if (!window.confirm("Delete this business?")) return;
    await apiFetch(`/admin/business/${selectedBusiness}`, savedToken, { method: "DELETE" });
    const nextBusinesses = businesses.filter((business) => business.business_id !== selectedBusiness);
    setBusinesses(nextBusinesses);
    setSelectedBusiness(nextBusinesses[0]?.business_id || "");
  }

  if (!savedToken) {
    return (
      <main style={styles.center}>
        <form onSubmit={handleLogin} style={styles.card}>
          <h1>Super Admin</h1>
          <p>Connect this dashboard to your FastAPI backend with the admin token.</p>
          <input value={token} onChange={(e) => setToken(e.target.value)} placeholder="Admin token" style={styles.input} />
          <button type="submit" style={styles.button}>Continue</button>
        </form>
      </main>
    );
  }

  return (
    <main style={styles.shell}>
      <section style={styles.sidebar}>
        <h2>Businesses</h2>
        <div style={{ display: "grid", gap: 10 }}>
          {businesses.map((business) => (
            <button key={business.business_id} onClick={() => setSelectedBusiness(business.business_id)} style={{ ...styles.businessButton, borderColor: selectedBusiness === business.business_id ? "#1d2328" : "#d8d0c7" }}>
              <strong>{business.name}</strong>
              <span>{business.whatsapp_number}</span>
            </button>
          ))}
        </div>
        <form onSubmit={handleCreateBusiness} style={{ display: "grid", gap: 10, marginTop: 20 }}>
          <h3>Create Business</h3>
          <input style={styles.input} placeholder="Name" value={businessForm.name} onChange={(e) => setBusinessForm({ ...businessForm, name: e.target.value })} />
          <input style={styles.input} placeholder="WhatsApp number" value={businessForm.whatsapp_number} onChange={(e) => setBusinessForm({ ...businessForm, whatsapp_number: e.target.value })} />
          <input style={styles.input} placeholder="Vendor phone" value={businessForm.vendor_phone} onChange={(e) => setBusinessForm({ ...businessForm, vendor_phone: e.target.value })} />
          <input style={styles.input} placeholder="Type" value={businessForm.business_type} onChange={(e) => setBusinessForm({ ...businessForm, business_type: e.target.value })} />
          <button type="submit" style={styles.button}>Create</button>
        </form>
      </section>
      <section style={styles.main}>
        <h1>AI Commerce Platform</h1>
        <p>Backend: {apiBase}</p>
        <div style={styles.metrics}>
          <Metric label="Messages" value={usage?.messages ?? 0} />
          <Metric label="Orders" value={usage?.orders ?? 0} />
          <Metric label="Customers" value={usage?.customers ?? 0} />
          <Metric label="Support" value={usage?.support_tickets ?? 0} />
        </div>
        <div style={{ display: "flex", justifyContent: "flex-end" }}>
          <button
            type="button"
            style={{ ...styles.button, background: businesses.length > 1 ? "#9a3412" : "#9b9b9b" }}
            disabled={businesses.length <= 1 || !selectedBusiness}
            onClick={handleDeleteBusiness}
          >
            Delete Selected Business
          </button>
        </div>
        <div style={styles.panel}>
          <h2>Orders</h2>
          <Table rows={selectedOrders} columns={["business_id", "order_id", "customer_phone", "status", "total"]} />
        </div>
        <div style={styles.panel}>
          <h2>Support Tickets</h2>
          <Table rows={selectedSupport} columns={["business_id", "ticket_id", "customer_phone", "issue", "status"]} />
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
    <div style={{ overflowX: "auto" }}>
      <table style={{ width: "100%", borderCollapse: "collapse" }}>
        <thead>
          <tr>{columns.map((column) => <th key={column} style={styles.th}>{column}</th>)}</tr>
        </thead>
        <tbody>
          {rows.map((row, index) => (
            <tr key={`${index}-${row[columns[0]]}`}>
              {columns.map((column) => <td key={column} style={styles.td}>{String(row[column] ?? "")}</td>)}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

const styles = {
  center: { minHeight: "100vh", display: "grid", placeItems: "center" },
  shell: { display: "grid", gridTemplateColumns: "320px 1fr", minHeight: "100vh" },
  sidebar: { padding: 24, borderRight: "1px solid #d8d0c7", background: "#fcfaf6" },
  main: { padding: 24, display: "grid", gap: 18 },
  card: { width: "min(420px, 92vw)", background: "#fff", padding: 24, borderRadius: 16, border: "1px solid #d8d0c7", display: "grid", gap: 12 },
  panel: { background: "#fff", borderRadius: 16, border: "1px solid #d8d0c7", padding: 18 },
  metrics: { display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: 12 },
  metric: { background: "#fff", borderRadius: 16, border: "1px solid #d8d0c7", padding: 18, display: "grid", gap: 8 },
  input: { padding: 10, borderRadius: 10, border: "1px solid #cfc6bb", width: "100%" },
  button: { padding: 10, borderRadius: 10, border: 0, background: "#1d2328", color: "#fff" },
  businessButton: { display: "grid", gap: 4, textAlign: "left", padding: 12, borderRadius: 12, background: "#fff", border: "1px solid #d8d0c7" },
  th: { textAlign: "left", borderBottom: "1px solid #d8d0c7", padding: "10px 8px" },
  td: { borderBottom: "1px solid #eee7dd", padding: "10px 8px" }
};
