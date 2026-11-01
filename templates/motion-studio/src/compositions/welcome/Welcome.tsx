// A three-second check that the studio renders: `npm run render -- welcome output/welcome.mp4`.
import React from "react";
import { AbsoluteFill, interpolate, spring, useCurrentFrame, useVideoConfig } from "remotion";

export const Welcome: React.FC = () => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const rise = spring({ frame, fps, config: { damping: 200 } });
  const fade = interpolate(frame, [60, 90], [1, 0], { extrapolateLeft: "clamp", extrapolateRight: "clamp" });
  return (
    <AbsoluteFill style={{ background: "#F5F5F7", alignItems: "center", justifyContent: "center" }}>
      <div style={{ fontFamily: "-apple-system, system-ui, sans-serif", fontSize: 120, fontWeight: 600, color: "#1D1D1F",
        letterSpacing: -3, opacity: rise * fade, transform: `translateY(${(1 - rise) * 40}px)` }}>
        Motion studio
      </div>
    </AbsoluteFill>
  );
};
