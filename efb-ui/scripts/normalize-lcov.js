import { readFile, writeFile } from "node:fs/promises";

const coveragePath = new URL("../coverage/lcov.info", import.meta.url);
const artifactPath = new URL("../lcov.info", import.meta.url);
const source = await readFile(coveragePath, "utf8");
const normalized = source.replace(/^SF:(.*)$/gm, (_, rawFile) => {
  const file = rawFile.replaceAll("\\", "/");
  return `SF:${file.startsWith("efb-ui/") ? file : `efb-ui/${file}`}`;
});
await writeFile(coveragePath, normalized);
await writeFile(artifactPath, normalized);
