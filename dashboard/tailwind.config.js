/** @type {import('tailwindcss').Config} */
module.exports = {
  content: ["./app/**/*.{js,jsx}", "./components/**/*.{js,jsx}", "./lib/**/*.{js,jsx}"],
  theme: {
    extend: {
      colors: {
        ink: "#15202b",
        sand: "#f4eee6",
        ember: "#af4d1f",
        moss: "#1d4f46",
        dusk: "#29334d"
      },
      boxShadow: {
        panel: "0 24px 70px rgba(20, 34, 47, 0.12)"
      },
      fontFamily: {
        sans: ["Manrope", "Segoe UI", "sans-serif"],
        mono: ["IBM Plex Mono", "Consolas", "monospace"]
      }
    }
  },
  plugins: []
};
