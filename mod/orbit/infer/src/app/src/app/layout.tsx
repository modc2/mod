import type { Metadata } from 'next';
import './globals.css';

export const metadata: Metadata = {
  title: 'infer — inference optimization',
  description:
    'One ONNX binary — onnxruntime on the server, onnxruntime-web in the tab.',
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
