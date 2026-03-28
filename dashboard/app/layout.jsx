import "./globals.css";

export const metadata = {
  title: "Super Admin Dashboard",
  description: "Multi-tenant AI commerce admin panel"
};

export default function RootLayout({ children }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
