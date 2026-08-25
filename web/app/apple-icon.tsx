import { ImageResponse } from "next/og";

/** Home-screen icon for iOS: same mark as app/icon.svg, rendered at 180px. */
export const size = { width: 180, height: 180 };
export const contentType = "image/png";

export default function AppleIcon() {
  return new ImageResponse(
    (
      <div
        style={{
          width: "100%",
          height: "100%",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          background: "#29a9e0",
          borderRadius: 40,
          color: "#04202e",
          fontSize: 84,
          fontWeight: 800,
          letterSpacing: -3,
          fontFamily: "Inter, sans-serif",
        }}
      >
        MD
      </div>
    ),
    size,
  );
}
