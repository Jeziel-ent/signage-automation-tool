import { forwardRef, useEffect, useRef } from "react";
import "./SpecularButton.css";

/**
 * A button with a glossy "specular" finish: a tinted gradient face, a thin bright edge whose highlight turns towards the
 * pointer (followMouse, within `proximity` px) and a soft light band sweeping across the face (autoAnimate). Everything
 * moves through CSS custom properties written from ONE requestAnimationFrame loop - no React re-render per frame - and the
 * loop only runs while something is animating. With prefers-reduced-motion the button is static (edge lit from the top).
 *
 * Props (all optional except children):
 *   tint / tintOpacity   gradient colour laid over baseColor (1 = fully the tint at the top-left)
 *   baseColor            the face's base fill
 *   textColor            label colour
 *   lineColor, thickness edge highlight colour and width (px)
 *   intensity            highlight strength (1 = normal; >1 brighter edge and sweep)
 *   shineSize, shineFade the sweep band's width and its soft edge, in % of the button width
 *   speed                sweeps per second of the auto animation (the edge light circles at the same rate)
 *   autoAnimate          keep sweeping continuously
 *   followMouse, proximity  aim the edge highlight at the pointer while it is within `proximity` px
 *   radius               corner radius (px); size: "sm" | "md" | "lg"; blur: backdrop blur (px) behind the face
 * Any other props (onClick, disabled, autoFocus, type, aria-*) go to the <button>.
 */
const SpecularButton = forwardRef(function SpecularButton(
  {
    children,
    className = "",
    size = "md",
    radius = 12,
    tint = "#dc2626",
    tintOpacity = 1,
    baseColor = "#ef4444",
    textColor = "#ffffff",
    lineColor = "#ffffff",
    thickness = 1.5,
    intensity = 1,
    shineSize = 20,
    shineFade = 30,
    speed = 0.5,
    autoAnimate = false,
    followMouse = false,
    proximity = 300,
    blur = 0,
    style,
    type = "button",
    ...rest
  },
  ref,
) {
  const localRef = useRef(null);
  const btnRef = ref || localRef;

  useEffect(() => {
    const el = btnRef.current;
    if (!el) return undefined;
    const reduce = typeof window !== "undefined" && window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
    if (reduce || (!autoAnimate && !followMouse)) {
      el.style.setProperty("--spb-angle", "0deg");
      el.style.setProperty("--spb-sweep", autoAnimate ? "0%" : "-200%");
      el.style.setProperty("--spb-near", "1");
      return undefined;
    }
    let raf = 0;
    let last = performance.now();
    let phase = 0; // 0..1 sweep position
    let pointer = null; // {x, y} in client px
    let near = 0; // eased 0..1 closeness of the pointer
    let aim = 0; // eased edge-light angle (deg)
    const onMove = (e) => { pointer = { x: e.clientX, y: e.clientY }; };
    const onLeave = () => { pointer = null; };
    if (followMouse) {
      window.addEventListener("pointermove", onMove, { passive: true });
      document.addEventListener("pointerleave", onLeave);
    }
    const tick = (now) => {
      const dt = Math.min(0.05, (now - last) / 1000);
      last = now;
      if (autoAnimate) phase = (phase + dt * speed) % 1;
      let targetNear = 0;
      let targetAim = autoAnimate ? phase * 360 : aim;
      if (followMouse && pointer) {
        const r = el.getBoundingClientRect();
        const cx = r.left + r.width / 2;
        const cy = r.top + r.height / 2;
        // distance from the pointer to the button's box (0 inside it)
        const dx = Math.max(r.left - pointer.x, 0, pointer.x - r.right);
        const dy = Math.max(r.top - pointer.y, 0, pointer.y - r.bottom);
        const dist = Math.hypot(dx, dy);
        targetNear = Math.max(0, 1 - dist / Math.max(1, proximity));
        if (targetNear > 0) {
          // conic gradients start at the top and run clockwise: 0deg = up
          const a = (Math.atan2(pointer.x - cx, cy - pointer.y) * 180) / Math.PI;
          // blend between the auto angle and the pointer angle by closeness (shortest way round)
          const base = targetAim;
          let d = ((a - base + 540) % 360) - 180;
          targetAim = base + d * targetNear;
        }
      }
      near += (targetNear - near) * Math.min(1, dt * 8);
      let da = ((targetAim - aim + 540) % 360) - 180;
      aim = (aim + da * Math.min(1, dt * (autoAnimate ? 20 : 10)) + 360) % 360;
      el.style.setProperty("--spb-angle", `${aim.toFixed(1)}deg`);
      el.style.setProperty("--spb-sweep", `${(-100 + phase * 300).toFixed(1)}%`);
      el.style.setProperty("--spb-near", (autoAnimate ? 0.55 + 0.45 * near : 0.35 + 0.65 * near).toFixed(3));
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => {
      cancelAnimationFrame(raf);
      window.removeEventListener("pointermove", onMove);
      document.removeEventListener("pointerleave", onLeave);
    };
  }, [autoAnimate, followMouse, proximity, speed, btnRef]);

  const vars = {
    "--spb-radius": `${radius}px`,
    "--spb-tint": tint,
    "--spb-tint-op": Math.max(0, Math.min(1, tintOpacity)),
    "--spb-base": baseColor,
    "--spb-text": textColor,
    "--spb-line": lineColor,
    "--spb-thick": `${thickness}px`,
    "--spb-int": intensity,
    "--spb-shine": `${shineSize}%`,
    "--spb-fade": `${shineFade}%`,
    "--spb-blur": `${blur}px`,
    ...style,
  };
  return (
    <button ref={btnRef} type={type} className={`spb spb-${size} ${className}`.trim()} style={vars} {...rest}>
      <span className="spb-face" aria-hidden="true" />
      <span className="spb-sweep" aria-hidden="true" />
      <span className="spb-edge" aria-hidden="true" />
      <span className="spb-label">{children}</span>
    </button>
  );
});

export default SpecularButton;
