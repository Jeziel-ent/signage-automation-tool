// Shared enter/exit motion for the workspace modals (Export, Print File, Generate ZIP). Each modal's backdrop and card are
// framer-motion elements spread with these, and the page renders the modal inside <AnimatePresence>, so closing plays the
// exit (fade + slight drop) instead of the modal vanishing. framer-motion honours prefers-reduced-motion for transforms.
export const BACKDROP_MOTION = {
  initial: { opacity: 0 },
  animate: { opacity: 1 },
  exit: { opacity: 0 },
  transition: { duration: 0.2, ease: "easeOut" },
};

export const CARD_MOTION = {
  initial: { opacity: 0, y: 12, scale: 0.97 },
  animate: { opacity: 1, y: 0, scale: 1 },
  exit: { opacity: 0, y: 8, scale: 0.98 },
  transition: { duration: 0.22, ease: [0.22, 1, 0.36, 1] },
};
