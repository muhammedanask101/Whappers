"use client";

import { create } from "zustand";

import { loginRequest, logoutRequest, refreshSession } from "@/lib/api";

export const useAuthStore = create((set, get) => ({
  accessToken: "",
  user: null,
  hydrated: true,
  bootstrapped: false,
  setSession: (payload) =>
    set({
      accessToken: payload?.access_token || "",
      user: payload?.user || null,
      bootstrapped: true
    }),
  clearSession: () => set({ accessToken: "", user: null, bootstrapped: true }),
  login: async (payload) => {
    const session = await loginRequest(payload);
    set({ accessToken: session.access_token, user: session.user, bootstrapped: true });
    return session;
  },
  restore: async () => {
    if (get().accessToken) {
      set({ bootstrapped: true });
      return get().user;
    }
    try {
      const session = await refreshSession();
      set({ accessToken: session.access_token, user: session.user, bootstrapped: true });
      return session.user;
    } catch {
      set({ accessToken: "", user: null, bootstrapped: true });
      return null;
    }
  },
  logout: async () => {
    try {
      await logoutRequest(get().accessToken);
    } finally {
      set({ accessToken: "", user: null, bootstrapped: true });
    }
  }
}));
