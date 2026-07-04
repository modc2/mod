import "./globals.css";
import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "freetune — CPU LoRA finetuning",
  description: "Finetune Qwen over a directory of code, on CPU, with an efficiency dashboard.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
