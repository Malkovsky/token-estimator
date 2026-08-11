import assert from "node:assert/strict";
import test from "node:test";
import { api } from "./api.ts";
import { designPath, designs, isDesignId } from "./designs.ts";

test("repository paths encode owner and repository", () => {
  const owner = encodeURIComponent("open ai");
  const repo = encodeURIComponent("skills/demo");
  assert.equal(owner, "open%20ai");
  assert.equal(repo, "skills%2Fdemo");
});

test("repository progress URL preserves the report query", () => {
  assert.equal(
    api.reportProgressUrl("open ai", "skills/demo", "a".repeat(40), "?encoding=o200k_base&path=cpp"),
    `/api/v1/repositories/github/open%20ai/skills%2Fdemo/commits/${"a".repeat(40)}/progress?encoding=o200k_base&path=cpp`,
  );
});

test("design comparison exposes five stable variants and equivalent screens", () => {
  assert.equal(designs.length, 5);
  assert.equal(new Set(designs.map((design) => design.id)).size, 5);
  for (const design of designs) {
    assert.equal(isDesignId(design.id), true);
    assert.equal(designPath(design.id), `/designs/${design.id}`);
    assert.equal(designPath(design.id, "report"), `/designs/${design.id}/report`);
    assert.equal(designPath(design.id, "local"), `/designs/${design.id}/local`);
  }
});
