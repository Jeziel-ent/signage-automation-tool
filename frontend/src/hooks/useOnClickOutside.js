import { useEffect } from "react";

/** Calls `handler` when a pointer press lands outside `ref`'s element (only while `active`). `ref` may be an array of refs
 *  (e.g. a trigger plus a portalled menu): a press inside ANY of them is "inside". */
export function useOnClickOutside(ref, handler, active = true) {
  useEffect(() => {
    if (!active) return undefined;
    const onDown = (e) => {
      const refs = Array.isArray(ref) ? ref : [ref];
      if (refs.some((r) => r.current && r.current.contains(e.target))) return;
      if (refs.some((r) => r.current)) handler(e);
    };
    document.addEventListener("mousedown", onDown);
    document.addEventListener("touchstart", onDown);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("touchstart", onDown);
    };
  }, [ref, handler, active]);
}
