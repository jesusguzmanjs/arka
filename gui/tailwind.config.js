/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{vue,js,ts,jsx,tsx}"],
  theme: {
    extend: {
      colors: {
        // surfaces (see .openspec/3-gui-spec.md §4)
        base: "#121212",
        panel: "#1c1c1e",
        elevated: "#232326",
        console: "#0d0d0f",
        // accent
        accent: {
          DEFAULT: "#4fd1c5",
          hover: "#6adfd4",
          pressed: "#3fb8ad",
        },
        // text
        primary: "#f2f2f2",
        muted: "#8a8a8e",
        dim: "#5a5a5e",
        // status
        success: "#4caf50",
        warn: "#e0a72e",
        error: "#e05c5c",
        // borders
        border: "#2a2a2e",
        "border-strong": "#3a3a3e",
      },
      fontFamily: {
        ui: ['Inter', '"Segoe UI"', "Avenir", "Helvetica", "Arial", "sans-serif"],
        mono: [
          "ui-monospace",
          '"Cascadia Code"',
          '"Fira Code"',
          '"JetBrains Mono"',
          "Consolas",
          "monospace",
        ],
      },
    },
  },
  plugins: [],
};
