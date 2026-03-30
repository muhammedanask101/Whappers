import "./globals.css";
import Providers from "./providers";

export const metadata = {
  title: "WhatsApp Commerce Dashboard",
  description: "Multi-tenant superadmin and business dashboard"
};

export default function RootLayout({ children }) {
  return (
    <html lang="en">
      <body>
        <Providers>{children}</Providers>
      </body>
    </html>
  );
}
