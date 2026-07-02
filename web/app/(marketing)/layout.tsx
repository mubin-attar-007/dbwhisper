import { Footer } from "../components/Footer";

export default function MarketingLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <>
      <div className="flex-1">{children}</div>
      <Footer />
    </>
  );
}
