import type { Finding, Grade } from "./types";

/** One colour per grade, shared by the panel and the drawing, so a finding and the rooms it
 *  names read as the same thing. */
export const GRADE_STYLE: Record<Grade, { label: string; ink: string; wash: string }> = {
  critical: { label: "Critical", ink: "#c62828", wash: "#fdf1f1" },
  major: { label: "Major", ink: "#a15c07", wash: "#fdf6e9" },
  minor: { label: "Minor", ink: "#5b6573", wash: "#f5f6f8" },
};

const ORDER: Grade[] = ["critical", "major", "minor"];

/** A finding's grade. Plans checked before findings were graded carry only a severity:
 *  an error was what is now critical, a warning what is now major. */
export function gradeOf(finding: Finding): Grade {
  return finding.grade ?? (finding.severity === "error" ? "critical" : "major");
}

/** Most severe first, keeping the report's order within a grade. */
export function bySeverity(a: Finding, b: Finding): number {
  return ORDER.indexOf(gradeOf(a)) - ORDER.indexOf(gradeOf(b));
}
