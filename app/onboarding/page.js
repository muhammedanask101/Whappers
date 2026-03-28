"use client";

import { useState } from "react";

const apiBase = process.env.NEXT_PUBLIC_API_BASE_URL || "http://localhost:8000";

export default function OnboardingPage() {
  const [form, setForm] = useState({
    name: "",
    phone: ""
  });

  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState(null);
  const [error, setError] = useState("");

  async function handleSubmit(e) {
    e.preventDefault();
    setLoading(true);
    setError("");

    try {
      const res = await fetch(`${apiBase}/onboard/`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json"
        },
        body: JSON.stringify(form)
      });

      if (!res.ok) {
        throw new Error("Failed to onboard");
      }

      const data = await res.json();
      setResult(data);
    } catch (err) {
      setError("Something went wrong. Try again.");
    } finally {
      setLoading(false);
    }
  }

  return (
    <main style={styles.container}>
      <div style={styles.card}>
        <h1>🚀 Setup Your AI Sales Agent</h1>
        <p>Create your business in 30 seconds</p>

        {!result && (
          <form onSubmit={handleSubmit} style={styles.form}>
            <input
              placeholder="Business Name"
              value={form.name}
              onChange={(e) =>
                setForm({ ...form, name: e.target.value })
              }
              style={styles.input}
              required
            />

            <input
              placeholder="WhatsApp / Phone Number"
              value={form.phone}
              onChange={(e) =>
                setForm({ ...form, phone: e.target.value })
              }
              style={styles.input}
              required
            />

            <button type="submit" style={styles.button} disabled={loading}>
              {loading ? "Creating..." : "Create My AI Agent"}
            </button>

            {error && <p style={styles.error}>{error}</p>}
          </form>
        )}

        {result && (
          <div style={styles.success}>
            <h2>✅ Your AI Agent is Ready!</h2>

            <p><strong>Business ID:</strong></p>
            <code style={styles.code}>{result.business_id}</code>

            <p style={{ marginTop: 15 }}>
              Save this ID. You’ll use it to manage your business.
            </p>

            <button
              style={styles.button}
              onClick={() => window.location.href = "/"}
            >
              Go to Dashboard
            </button>
          </div>
        )}
      </div>
    </main>
  );
}

const styles = {
  container: {
    minHeight: "100vh",
    display: "grid",
    placeItems: "center",
    background: "#f7f7f7"
  },
  card: {
    width: "400px",
    background: "#fff",
    padding: 30,
    borderRadius: 12,
    boxShadow: "0 10px 30px rgba(0,0,0,0.1)"
  },
  form: {
    display: "grid",
    gap: 12,
    marginTop: 20
  },
  input: {
    padding: 12,
    border: "1px solid #ddd",
    borderRadius: 8
  },
  button: {
    padding: 12,
    background: "#111",
    color: "#fff",
    borderRadius: 8,
    border: "none",
    cursor: "pointer"
  },
  error: {
    color: "red"
  },
  success: {
    textAlign: "center"
  },
  code: {
    display: "block",
    padding: 10,
    background: "#eee",
    borderRadius: 6,
    marginTop: 10
  }
};