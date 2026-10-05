/**
 * Hook to access the crew platform context.
 */

import { useContext } from "react";
import { CrewContext } from "./CrewPlatformContext.jsx";

export function useCrewPlatform() {
  const ctx = useContext(CrewContext);
  if (!ctx) throw new Error("useCrewPlatform must be inside CrewPlatformProvider");
  return ctx;
}
