#!/usr/bin/env node
/**
 * Fail CI only on newly introduced HIGH/CRITICAL npm advisories.
 *
 * Pre-existing high/critical findings are tracked in the baseline file
 * docs/security-baseline-npm-audit.json (documented in
 * docs/ci-quality-gate.md) and reported but do not block. New ones do.
 * The full report is always uploaded as an artifact regardless of exit
 * code, so nothing is hidden from view.
 */
const fs = require("fs");
const path = require("path");

function main(argv) {
  const reportPath = argv[0];
  if (!reportPath) {
    console.log("usage: check_npm_audit.js <npm-audit.json>");
    process.exit(2);
  }

  const baselinePath = path.join(__dirname, "..", "..", "docs", "security-baseline-npm-audit.json");
  let baseline = new Set();
  if (fs.existsSync(baselinePath)) {
    const b = JSON.parse(fs.readFileSync(baselinePath, "utf8"));
    baseline = new Set(b.known_ids || []);
  }

  const report = JSON.parse(fs.readFileSync(reportPath, "utf8"));
  const vulns = report.vulnerabilities || {};

  const found = new Set();
  for (const [name, info] of Object.entries(vulns)) {
    if (info.severity === "high" || info.severity === "critical") {
      for (const via of info.via || []) {
        if (via && typeof via === "object" && via.url) {
          found.add(`${name}::${via.url}`);
        }
      }
      if ((info.via || []).length === 0 || (info.via || []).every((v) => typeof v === "string")) {
        found.add(`${name}::${info.severity}`);
      }
    }
  }

  const newFindings = [...found].filter((id) => !baseline.has(id));
  const preExisting = [...found].filter((id) => baseline.has(id));

  if (preExisting.length) {
    console.log(`Pre-existing HIGH/CRITICAL (tracked in baseline, not blocking): ${preExisting.length}`);
    preExisting.sort().forEach((id) => console.log(`  [baseline] ${id}`));
  }

  if (newFindings.length) {
    console.log(`NEW HIGH/CRITICAL npm advisories: ${newFindings.length}`);
    newFindings.sort().forEach((id) => console.log(`  [NEW] ${id}`));
    console.log(
      "Blocking: newly introduced HIGH/CRITICAL npm vulnerabilities. " +
        "Run `npm audit fix`, upgrade the dependency, or add a justified " +
        "entry to docs/security-baseline-npm-audit.json."
    );
    process.exit(1);
  }

  console.log("No newly introduced HIGH/CRITICAL npm-audit findings.");
}

main(process.argv.slice(2));
