// import type { Config } from "tailwindcss";

// /**
//  * Design tokens.
//  *
//  * Colour is used semantically, never decoratively — in an adjudication
//  * console the palette carries meaning a reviewer acts on:
//  *
//  *   provenance tiers   committed > network > submitted > asserted
//  *   outcomes           merchant / card member / split / insufficient
//  *   severities         low → critical
//  *
//  * Each has a fixed hue, so a reviewer reads state by colour before reading
//  * the label and the same hue means the same thing on every screen. Colour
//  * is never the ONLY channel: every badge also carries text, because a
//  * dispute decision must be legible to a colour-blind reviewer.
//  */
// export default {
//   content: ["./index.html", "./src/**/*.{ts,tsx}"],
//   darkMode: "class",
//   theme: {
//     extend: {
//       colors: {
//         // Institutional blue, reserved for primary actions and focus rings.
//         brand: {
//           50: "#eef4ff", 100: "#dae6ff", 200: "#bcd3ff", 300: "#8fb5ff",
//           400: "#5a8dff", 500: "#3366ff", 600: "#1f47eb", 700: "#1a37c4",
//           800: "#1b309e", 900: "#1c2f7d", 950: "#141d4d",
//         },
//       },
//       fontFamily: {
//         sans: ["Inter", "ui-sans-serif", "system-ui", "-apple-system", "Segoe UI", "Roboto", "sans-serif"],
//         mono: ["ui-monospace", "SFMono-Regular", "Menlo", "Consolas", "monospace"],
//       },
//       fontSize: {
//         "2xs": ["0.6875rem", { lineHeight: "1rem" }],
//       },
//       keyframes: {
//         "fade-in": { from: { opacity: "0", transform: "translateY(2px)" }, to: { opacity: "1", transform: "none" } },
//         shimmer: { "100%": { transform: "translateX(100%)" } },
//       },
//       animation: {
//         "fade-in": "fade-in 160ms ease-out",
//         shimmer: "shimmer 1.6s infinite",
//       },
//     },
//   },
//   plugins: [],
// } satisfies Config;

import type { Config } from 'tailwindcss'

export default {
  content: [
    "./index.html",
    "./src/**/*.{js,ts,jsx,tsx}",
  ],
  darkMode: 'class', // Ensures dark mode triggers correctly
  theme: {
    extend: {
      colors: {
        border: "oklch(var(--border) / <alpha-value>)",
        input: "oklch(var(--input) / <alpha-value>)",
        ring: "oklch(var(--ring) / <alpha-value>)",
        background: "oklch(var(--background) / <alpha-value>)",
        foreground: "oklch(var(--foreground) / <alpha-value>)",
        primary: {
          DEFAULT: "oklch(var(--primary) / <alpha-value>)",
          foreground: "oklch(var(--primary-foreground) / <alpha-value>)",
        },
        secondary: {
          DEFAULT: "oklch(var(--secondary) / <alpha-value>)",
          foreground: "oklch(var(--secondary-foreground) / <alpha-value>)",
        },
        destructive: {
          DEFAULT: "oklch(var(--destructive) / <alpha-value>)",
          foreground: "oklch(var(--primary-foreground) / <alpha-value>)",
        },
        muted: {
          DEFAULT: "oklch(var(--muted) / <alpha-value>)",
          foreground: "oklch(var(--muted-foreground) / <alpha-value>)",
        },
        accent: {
          DEFAULT: "oklch(var(--accent) / <alpha-value>)",
          foreground: "oklch(var(--accent-foreground) / <alpha-value>)",
        },
        popover: {
          DEFAULT: "oklch(var(--popover) / <alpha-value>)",
          foreground: "oklch(var(--popover-foreground) / <alpha-value>)",
        },
        card: {
          DEFAULT: "oklch(var(--card) / <alpha-value>)",
          foreground: "oklch(var(--card-foreground) / <alpha-value>)",
        },
      },
      borderRadius: {
        lg: "var(--radius)",
        md: "calc(var(--radius) - 2px)",
        sm: "calc(var(--radius) - 4px)",
      },
    },
  },
  plugins: [],
} satisfies Config