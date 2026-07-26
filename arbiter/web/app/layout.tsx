import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "ARBITER",
  description: "Auditable dispute adjudication",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>
        <div className="min-h-screen">{children}</div>
      </body>
    </html>
  );
}
