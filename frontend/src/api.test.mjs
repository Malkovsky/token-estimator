import assert from "node:assert/strict";
import test from "node:test";
import { designPath, designs, isDesignId } from "./designs.ts";

test("repository paths encode owner and repository", () => {
  const owner = encodeURIComponent("open ai");
  const repo = encodeURIComponent("skills/demo");
  assert.equal(owner, "open%20ai");
  assert.equal(repo, "skills%2Fdemo");
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
