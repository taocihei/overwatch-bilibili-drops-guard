import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "守望先锋 B站直播挂宝 · 赞助服务",
  description: "为守望先锋 B站直播挂宝提供安全、克制的扫码赞助服务。",
  icons: {
    icon: "/favicon.svg",
    shortcut: "/favicon.svg",
  },
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="zh-CN">
      <body>{children}</body>
    </html>
  );
}