export const designs = [
  {
    id: "atlas",
    name: "Atlas",
    direction: "Editorial clarity",
    description: "Warm, spacious, explanatory, and approachable for first-time visitors.",
    traits: ["Generous type", "Calm hierarchy", "Human tone"],
  },
  {
    id: "workbench",
    name: "Workbench",
    direction: "Developer console",
    description: "Dense, keyboard-minded, and optimized for scanning technical inventories.",
    traits: ["Dark shell", "Compact rows", "Tool-first"],
  },
  {
    id: "blueprint",
    name: "Blueprint",
    direction: "Structured dashboard",
    description: "A crisp technical system with strong grids, modules, and measurable structure.",
    traits: ["Blue grid", "Bento metrics", "Precise borders"],
  },
  {
    id: "library",
    name: "Library",
    direction: "Repository archive",
    description: "Quiet, document-led navigation inspired by research catalogs and indexes.",
    traits: ["Serif voice", "Index layout", "Reading focus"],
  },
  {
    id: "signal",
    name: "Signal",
    direction: "Command center",
    description: "High-contrast, energetic, and metrics-forward for a modern systems product.",
    traits: ["Neon accents", "Card matrix", "Strong telemetry"],
  },
] as const;

export type DesignId = (typeof designs)[number]["id"];
export type DesignScreen = "landing" | "report" | "local";

export const CANONICAL_DESIGN: DesignId = "blueprint";

export const sampleRepository = {
  owner: "Malkovsky",
  repository: "ai_for_cpp",
  sha: "c723c1e81354f3d40ad00766f432bf66e5c27a2b",
  search: "?encoding=o200k_base",
};

export function isDesignId(value: string | null | undefined): value is DesignId {
  return designs.some((design) => design.id === value);
}

export function designPath(design: DesignId, screen: DesignScreen = "landing"): string {
  if (screen === "landing") return `/designs/${design}`;
  return `/designs/${design}/${screen}`;
}
