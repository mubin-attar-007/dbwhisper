import { ImageResponse } from "next/og";

// Branded Open Graph image for link previews. Rendered by satori, so it must stay
// satori-safe: inline styles only, explicit display:flex on any element with more
// than one child, system fonts, no network fetches. No edge runtime — the image is
// static, so let Next prerender it at build time.
export const alt = "DBWhisper — Ask your database anything.";
export const size = { width: 1200, height: 630 };
export const contentType = "image/png";

export default function OpengraphImage() {
  return new ImageResponse(
    (
      <div
        style={{
          width: "100%",
          height: "100%",
          display: "flex",
          flexDirection: "column",
          justifyContent: "center",
          padding: "96px",
          backgroundColor: "#0f172a",
          backgroundImage:
            "radial-gradient(1000px 500px at 78% 12%, rgba(99,102,241,0.28), transparent 60%)",
          fontFamily:
            "ui-sans-serif, system-ui, -apple-system, Segoe UI, Roboto, Helvetica, Arial, sans-serif",
        }}
      >
        {/* Brand mark: indigo database glyph + eyebrow label */}
        <div
          style={{
            display: "flex",
            alignItems: "center",
            gap: "20px",
            marginBottom: "40px",
          }}
        >
          <div
            style={{
              display: "flex",
              width: "72px",
              height: "72px",
              borderRadius: "18px",
              background: "#6366f1",
              alignItems: "center",
              justifyContent: "center",
              fontSize: "40px",
              color: "#eef2ff",
              fontWeight: 700,
            }}
          >
            DB
          </div>
          <div
            style={{
              display: "flex",
              fontSize: "26px",
              letterSpacing: "0.32em",
              textTransform: "uppercase",
              color: "#a5b4fc",
              fontWeight: 600,
            }}
          >
            DBWhisper
          </div>
        </div>

        <div
          style={{
            display: "flex",
            fontSize: "104px",
            lineHeight: 1.05,
            fontWeight: 800,
            color: "#f8fafc",
            letterSpacing: "-0.03em",
          }}
        >
          Ask your database anything.
        </div>

        <div
          style={{
            display: "flex",
            marginTop: "36px",
            fontSize: "38px",
            color: "#94a3b8",
            fontWeight: 400,
          }}
        >
          Natural language to SQL, plus results — in seconds.
        </div>

        {/* Indigo accent underline */}
        <div
          style={{
            display: "flex",
            marginTop: "56px",
            width: "220px",
            height: "8px",
            borderRadius: "9999px",
            background: "#6366f1",
          }}
        />
      </div>
    ),
    { ...size },
  );
}
