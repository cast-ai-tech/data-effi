import type { MetadataRoute } from "next";

/** So "Añadir a pantalla de inicio" on a phone shows the right name and icon. */
export default function manifest(): MetadataRoute.Manifest {
  return {
    name: "Master Data",
    short_name: "Master Data",
    description: "Tu operación de contraentrega en números, país por país.",
    start_url: "/",
    display: "standalone",
    background_color: "#f4f6f9",
    theme_color: "#f4f6f9",
    lang: "es",
    icons: [{ src: "/icon.svg", sizes: "any", type: "image/svg+xml" }],
  };
}
