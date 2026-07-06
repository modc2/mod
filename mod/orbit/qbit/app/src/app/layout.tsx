import "./globals.css";

export const metadata = {
  title: "qbit",
  description: "Quantum-resistant key-value store",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en">
      <body className="bg-black text-white min-h-screen">{children}</body>
    </html>
  );
}
