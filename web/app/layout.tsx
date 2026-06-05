import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "DBWhisper — NL → SQL Console",
  description:
    "Ask questions in plain English and get SQL plus results from the DBWhisper API.",
};

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en" className="dark">
      <body className="min-h-screen bg-slate-950 text-slate-100">
        {children}
      </body>
    </html>
  );
}
