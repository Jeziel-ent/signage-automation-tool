// Runs the frontend tests exactly as `npm test` does (same file list, read from package.json) and writes lcov coverage to
// frontend/lcov.info. Run from frontend/ (the Jenkinsfile does).
import { readFileSync } from "node:fs";
import { spawnSync } from "node:child_process";

const pkg = JSON.parse(readFileSync("package.json", "utf8"));
const script = pkg.scripts?.test ?? "";
if (!script.startsWith("node --test ")) {
  console.error('package.json "test" script is not "node --test <files>": cannot derive the file list');
  process.exit(2);
}
const files = script.slice("node --test ".length).split(/\s+/).filter(Boolean);

const result = spawnSync(
  process.execPath,
  [
    "--test",
    "--experimental-test-coverage",
    "--test-reporter=spec", "--test-reporter-destination=stdout",
    "--test-reporter=lcov", "--test-reporter-destination=lcov.info",
    ...files,
  ],
  { stdio: "inherit" },
);
process.exit(result.status ?? 1);
