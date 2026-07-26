import type { Config } from "tailwindcss";

const config: Config = {
  content: ["./app/**/*.{ts,tsx}", "./components/**/*.{ts,tsx}"],
  darkMode: "media",
  theme: {
    extend: {
      colors: {
        merchant: { win: "#166534", lose: "#991b1b" },
        tier: {
          committed: "#0f766e",
          network: "#1d4ed8",
          submitted: "#a16207",
          asserted: "#6b7280",
        },
      },
    },
  },
  plugins: [],
};

export default config;
