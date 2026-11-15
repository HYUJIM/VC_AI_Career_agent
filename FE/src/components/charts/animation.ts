import type { Transition } from "motion/react";

export const DEFAULT_ANIMATION_DURATION_MS = 1100;

/** Default enter transition — matches the original line chart reveal. */
export const DEFAULT_CHART_ENTER_TRANSITION: Transition = {
  type: "tween",
  duration: DEFAULT_ANIMATION_DURATION_MS / 1000,
  ease: [0.85, 0, 0.15, 1],
};
