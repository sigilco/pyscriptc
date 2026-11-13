// The shape of data/compat/compat.json (schema version 2). Validated by
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

// The plane split of one package tree: how many definitions lower natively,
// how many route to the island, and how many stay blocked.
export interface PackagePlane {
  definitions: number;
  native: number;
  island: number;
  blocked: number;
}

// The island verdict, the second axis of the package rows. It comes from a
// run receipt, so `unmeasured` means nobody ran it yet.
export type IslandStatus = "runs" | "blocked" | "unmeasured";

export interface PackageRow {
  name: string;
  import_name: string;
  version: string;
  status: "full" | "partial" | "none";
  island: IslandStatus;
  plane: PackagePlane;
  evidence: string;
  fences: number;
  definitions: number;
  fenced_definitions: number;
  top_families: PackageFamily[];
  codes: FenceCode[];
  pypi: string;
}

// The island section: the run receipts behind the per-package verdicts.
export interface IslandSection {
  measured: number;
  packages: number;
  runs: number;
  date: string;
  commit: string;
  source: string;
}

// The install channel the measurement came from.
export interface CompatChannel {
  released: string;
  install_released: string;
  install_master: string;
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
  schema: "2";
  generated: string;
  commit: string;
  sources: CompatSources;
  summary: CompatSummary;
  channel: CompatChannel;
  island: IslandSection;
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

// The island verdict, in the site's own words.
export const ISLAND_LABEL: Record<IslandStatus, string> = {
  runs: "runs on the island",
  blocked: "blocked",
  unmeasured: "not measured",
};

export const ISLAND_COLOR: Record<IslandStatus, string> = {
  runs: "#10b981",
  blocked: "#ef4444",
  unmeasured: "#e5e7eb",
};
