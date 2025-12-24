import './globals.css';

export const metadata = {
  title: 'Newma - Base Network',
  description: 'Newma Smart Contract on Base',
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
