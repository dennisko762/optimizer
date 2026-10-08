const fs = require("fs");

const [outPath, reportPath] = process.argv.slice(2);
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

const ids = [...found].sort();
console.log(`${ids.length} HIGH/CRITICAL npm advisory ids collected`);

fs.writeFileSync(
  outPath,
  JSON.stringify(
    {
      _comment:
        "Pre-existing npm-audit HIGH/CRITICAL findings on the audited baseline commit, tracked so CI only blocks NEW vulnerabilities introduced by a PR. See docs/ci-quality-gate.md.",
      baseline_commit: "86e632c8438083cadc5d4967db72d2b0399bb2d3",
      known_ids: ids,
    },
    null,
    2
  ) + "\n"
);
