import { ImageResponse } from "next/og";

// Apple touch icon. Kept satori-safe (inline styles, system font, no network fetch)
// and visually aligned with app/icon.svg: indigo "DB" mark on a slate rounded square.
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
          background: "#0f172a",
          fontFamily:
            "ui-sans-serif, system-ui, -apple-system, Segoe UI, Roboto, sans-serif",
        }}
      >
        <div
          style={{
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            width: "132px",
            height: "132px",
            borderRadius: "34px",
            background: "#6366f1",
            color: "#eef2ff",
            fontSize: "68px",
            fontWeight: 700,
          }}
        >
          DB
        </div>
      </div>
    ),
    { ...size },
  );
}
