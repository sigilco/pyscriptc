// The shape of data/compat/compat.json (schema version 1). Validated by
// scripts/compat_schema.py at generation time; these types mirror it for the
// page build. Import as JSON via resolveJsonModule.

export interface CompatSources {
  support_data: string;
  pins: string;
  coverage: string;
  score: string;
}

export interface CompatSummary {
  builtins_full: number;
  builtins_partial: number;
  builtins_none: number;
  stdlib_full: number;
  stdlib_partial: number;
  stdlib_none: number;
  packages_full: number;
  packages_partial: number;
  packages_none: number;
  weighted_pass: number;
  fences: number;
}

export interface SupportRow {
  id: string;
  kind: "builtin" | "stdlib";
  label: string;
  names: string[];
  status: "full" | "partial" | "none";
  count: number;
  unsupported: string[];
  fences: string[];
  note: string;
}

export interface FenceCode {
  code: string;
  count: number;
}

export interface PackageFamily {
  family: string;
  count: number;
}

export interface PackageRow {
  name: string;
  import_name: string;
  version: string;
  status: "full" | "partial" | "none";
  fences: number;
  definitions: number;
  fenced_definitions: number;
  top_families: PackageFamily[];
  codes: FenceCode[];
  pypi: string;
}

export interface LadderRung {
  rung: string;
  label: string;
  corpus: string;
  total: number;
  compiled: number;
  passed: number;
  measured: boolean;
}

export interface LadderLane {
  lane: string;
  imports: number;
  share: number;
}

export interface LadderBlocker {
  family: string;
  rootCause: string;
  scriptsBlocked: number;
  scriptsAlone: number;
}

export interface LadderRootCause {
  cause: string;
  scriptsReleased: number;
}

export interface LadderR0Point {
  date: string;
  passed: number;
  total: number;
  rate: number;
}

export interface LadderR0 {
  rung: string;
  label: string;
  corpus: string;
  total: number;
  passed: number;
  rate: number;
  fences: number;
  date: string;
  blockers: LadderBlocker[];
  rootCauses: LadderRootCause[];
  history: LadderR0Point[];
}

export interface Ladder {
  corpus: string;
  rungs: LadderRung[];
  lanes: LadderLane[];
  selfContainedShare: number;
  trees: number;
  r0?: LadderR0;
}

export interface FenceCensusRow {
  code: string;
  title: string;
  count: number;
  packages: number;
}

export interface HistoryPoint {
  date: string;
  commit: string;
  weighted_pass: number;
  packages_full: number;
  packages_partial: number;
  packages_none: number;
  fences: number;
}

export interface CompatData {
  schema: "1";
  generated: string;
  commit: string;
  sources: CompatSources;
  summary: CompatSummary;
  builtins: SupportRow[];
  stdlib: SupportRow[];
  packages: PackageRow[];
  language: SupportRow[];
  ladder: Ladder;
  fences: FenceCensusRow[];
  history: HistoryPoint[];
}

export type Status = "full" | "partial" | "none";

export const STATUS_COLOR: Record<Status | "unknown", string> = {
  full: "#10b981",
  partial: "#f59e0b",
  none: "#ef4444",
  unknown: "#e5e7eb",
};

export const STATUS_LABEL: Record<Status | "unknown", string> = {
  full: "full",
  partial: "partial",
  none: "none",
  unknown: "unknown",
};
