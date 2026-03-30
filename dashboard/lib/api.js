const apiBase = process.env.NEXT_PUBLIC_API_BASE_URL || "http://localhost:8000";

async function parseJson(response) {
  const text = await response.text();
  if (!text) return null;
  try {
    return JSON.parse(text);
  } catch {
    return { success: false, data: null, error: text };
  }
}

export async function apiRequest(path, { token, method = "GET", body, headers } = {}) {
  const response = await fetch(`${apiBase}${path}`, {
    method,
    credentials: "include",
    headers: {
      "Content-Type": "application/json",
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...(headers || {})
    },
    body: body ? JSON.stringify(body) : undefined
  });

  const payload = await parseJson(response);
  if (!response.ok) {
    const message = payload?.error || payload?.detail || `Request failed with status ${response.status}`;
    const error = new Error(message);
    error.status = response.status;
    throw error;
  }

  if (payload && payload.success === false) {
    throw new Error(payload.error || "Request failed.");
  }

  return payload?.data ?? payload;
}

export async function refreshSession() {
  return apiRequest("/api/auth/refresh", { method: "POST" });
}

export async function loginRequest(payload) {
  return apiRequest("/api/auth/login", { method: "POST", body: payload });
}

export async function signupRequest(payload) {
  return apiRequest("/api/auth/signup", { method: "POST", body: payload });
}

export async function logoutRequest(token) {
  return apiRequest("/api/auth/logout", { method: "POST", token });
}

export { apiBase };
