const fs = require("fs");
const path = require("path");

const root = path.resolve(__dirname, "..");
const pkg = require(path.join(root, "package.json"));

const requiredFiles = [
  "hub/static/scss/custom.scss",
  "hub/static/css/custom.css",
];

for (const file of requiredFiles) {
  if (!fs.existsSync(path.join(root, file))) {
    throw new Error(`Missing static asset: ${file}`);
  }
}

if (!pkg.dependencies || !pkg.dependencies.sass) {
  throw new Error("Missing sass package dependency");
}

const buildCss = pkg.scripts && pkg.scripts["build-css"];
if (
  !buildCss ||
  !buildCss.includes("hub/static/scss/custom.scss") ||
  !buildCss.includes("hub/static/css/custom.css")
) {
  throw new Error("build-css script does not compile the expected Sass asset");
}

console.log("npm package smoke test passed");
