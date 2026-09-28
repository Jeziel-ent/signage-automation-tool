import { useEffect, useRef, useState } from "react";
import { animate, useReducedMotion } from "framer-motion";

/**
 * A number that counts to its new value instead of jumping (the workspace badges: shops loaded, jobs). Eases over ~0.6 s
 * from whatever it currently shows, so a quick run of changes glides rather than restarting from 0; with
 * prefers-reduced-motion it just shows the value. Tabular figures keep the badge from jittering in width while it counts.
 */
export default function AnimatedCount({ value, duration = 0.6 }) {
  const reduce = useReducedMotion();
  const [shown, setShown] = useState(value);
  const current = useRef(value);
  useEffect(() => {
    if (reduce) {
      current.current = value;
      setShown(value);
      return undefined;
    }
    const controls = animate(current.current, value, {
      duration,
      ease: [0.22, 1, 0.36, 1],
      onUpdate: (v) => {
        current.current = v;
        setShown(Math.round(v));
      },
    });
    return () => controls.stop();
  }, [value, duration, reduce]);
  return <span className="count-num">{shown}</span>;
}
