import type { Metadata, Viewport } from "next";
import { Inter } from "next/font/google";
import "./globals.css";

const inter = Inter({
  subsets: ["latin"],
  display: "swap",
  variable: "--font-inter",
});

export const metadata: Metadata = {
  metadataBase: new URL(
    process.env.NEXT_PUBLIC_SITE_URL ?? "http://localhost:3000",
  ),
  title: {
    default: "DBWhisper — Natural Language to SQL",
    template: "%s · DBWhisper",
  },
  description:
    "Ask questions in plain English and get SQL plus results from the DBWhisper API.",
  openGraph: {
    title: "DBWhisper — Natural Language to SQL",
    description:
      "Ask questions in plain English and get SQL plus results from the DBWhisper API.",
    siteName: "DBWhisper",
    type: "website",
  },
  twitter: {
    card: "summary",
  },
};

export const viewport: Viewport = {
  themeColor: "#0f172a",
  colorScheme: "dark",
  width: "device-width",
  initialScale: 1,
};

// Chrome (auth, footer) now lives in the route-group layouts: (app) for the console,
// (marketing) for the landing page. The root only owns <html>/<body>, fonts, and metadata.
export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en" className={`dark ${inter.variable}`}>
      <body className="flex min-h-screen flex-col bg-slate-950 text-slate-100">
        {children}
      </body>
    </html>
  );
}
