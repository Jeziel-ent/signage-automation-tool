import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { fitTransform, textStyle } from "./textFormat.js";

// The canvas shows CorelDRAW's own render of each shape, which can't be re-typeset in the browser. A text object whose content or font
// was changed (while typing, or after the `text` op) is drawn instead as a real SVG <text>: in the object's own box, fitted to the box
// height by measuring the glyphs, in the colour sampled from CorelDRAW's render of it. An approximation until Save and Generate
// produces CorelDRAW's real result (font metrics, kerning and alignment differ).

const FALLBACK_FONTS = `"Nirmala UI", "Nirmala Text", "Noto Sans Tamil", "Latha", "InaiMathi", "Lohit Tamil", sans-serif`;
const colorCache = new Map(); // image url -> css colour (or a pending promise)

/** Average colour of the opaque pixels of `url` (the text's glyphs), e.g. "rgb(255,255,255)". */
function sampleColor(url) {
  if (colorCache.has(url)) return colorCache.get(url);
  const p = new Promise((resolve) => {
    const img = new Image();
    img.crossOrigin = "anonymous";
    img.onload = () => {
      try {
        const W = Math.min(512, Math.max(1, img.naturalWidth));
        const H = Math.max(1, Math.round((W * img.naturalHeight) / Math.max(1, img.naturalWidth)));
        const c = document.createElement("canvas");
        c.width = W;
        c.height = H;
        const g = c.getContext("2d", { willReadFrequently: true });
        g.drawImage(img, 0, 0, W, H);
        const d = g.getImageData(0, 0, W, H).data;
        // alpha-weighted, so thin glyphs (never fully opaque once scaled) still count, while faint anti-aliased edges count little
        let r = 0, gg = 0, b = 0, n = 0;
        for (let i = 0; i < d.length; i += 4) {
          const a = d[i + 3];
          if (a < 32) continue;
          r += d[i] * a; gg += d[i + 1] * a; b += d[i + 2] * a; n += a;
        }
        resolve(n ? `rgb(${Math.round(r / n)},${Math.round(gg / n)},${Math.round(b / n)})` : null);
      } catch {
        resolve(null);
      }
    };
    img.onerror = () => resolve(null);
    img.src = url;
  }).then((col) => {
    colorCache.set(url, col);
    return col;
  });
  colorCache.set(url, p);
  return p;
}

function useSampledColor(url) {
  const cached = url ? colorCache.get(url) : null;
  const [color, setColor] = useState(typeof cached === "string" ? cached : null);
  useEffect(() => {
    if (!url) return undefined;
    let alive = true;
    Promise.resolve(sampleColor(url)).then((c) => alive && setColor(c));
    return () => {
      alive = false;
    };
  }, [url]);
  return color;
}

/**
 * `node` (page mm, origin bottom-left) drawn as live text with `content`/`font`. The outer <svg> takes the same x/y/width/height
 * attributes as the shape's <image> and is registered in `imgRefs`, so a drag moves/resizes it exactly like an image.
 */
export default function LiveText({ node, content, font, origLines = 1, pageH, assetBase, imgRefs, outline = false }) {
  const color = useSampledColor(node.image ? assetBase + node.image.file : null) || "#111";
  const textRef = useRef(null);
  const [fit, setFit] = useState(null); // transform placing the measured glyph box onto the node box
  const lines = String(content ?? "").split(/\r\n|\r|\n/);
  const fontSize = 100; // measuring size, in viewBox units; the fit transform scales it to the box
  const family = `"${font || node.text.font || ""}", ${FALLBACK_FONTS}`;
  const { w, h } = node;
  const style = textStyle(node.text);
  const styleKey = JSON.stringify(style);

  useLayoutEffect(() => {
    let alive = true;
    const measure = () => {
      const el = textRef.current;
      if (!alive || !el) return;
      let bb;
      try {
        bb = el.getBBox();
      } catch {
        return;
      }
      if (!bb || !(bb.width > 0) || !(bb.height > 0)) return setFit(null);
      // The box height belongs to the ORIGINAL text (a text op never changes the box), so fit per line: one line of the new text gets
      // the height one line of the original had. The width follows naturally (a longer name runs wider, as it would in CorelDRAW).
      setFit(fitTransform(bb, { w, h, lines: lines.length, origLines, fontSize, style }));
    };
    measure();
    // Re-fit whenever a font finishes loading - the board's fonts are fetched in the background (utils/fontLoader.js), possibly after
    // this text was first drawn in a fallback font, and a different font means different glyph metrics.
    const fonts = document.fonts;
    if (fonts && fonts.ready) fonts.ready.then(measure);
    if (fonts && fonts.addEventListener) fonts.addEventListener("loadingdone", measure);
    return () => {
      alive = false;
      if (fonts && fonts.removeEventListener) fonts.removeEventListener("loadingdone", measure);
    };
  }, [content, family, w, h, origLines, lines.length, styleKey]); // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <svg
      ref={(el) => {
        if (el) imgRefs.current.set(node.id, el);
        else imgRefs.current.delete(node.id);
      }}
      data-id={node.id}
      x={node.x}
      y={pageH - node.y - node.h}
      width={w}
      height={h}
      viewBox={`0 0 ${w} ${h}`}
      preserveAspectRatio="none"
      overflow="visible"
      pointerEvents="none"
    >
      {outline && <rect width={w} height={h} fill="none" stroke="var(--color-red)" strokeWidth={Math.max(w, h) / 400} strokeDasharray={`${h / 12} ${h / 12}`} opacity="0.7" vectorEffect="non-scaling-stroke" />}
      <g transform={fit || undefined} style={{ visibility: fit ? "visible" : "hidden" }}>
        <text ref={textRef} textAnchor={style.anchor} xmlLang="ta" fill={color}
          style={{ fontFamily: family, fontSize, fontWeight: style.fontWeight, fontStyle: style.fontStyle, textDecoration: style.textDecoration,
            letterSpacing: style.letterSpacingEm ? `${style.letterSpacingEm * fontSize}px` : undefined }}>
          {lines.map((line, i) => (
            <tspan key={i} x="0" y={i * fontSize * style.lineEm}>{line || " "}</tspan>
          ))}
        </text>
      </g>
    </svg>
  );
}
