// Copies the parts of basecoat-css we vendor into the Flask app:
//   1. Jinja macros  -> app/templates/basecoat/
//   2. JS runtime     -> app/static/js/vendor/
//
// Run automatically after `npm install` (see package.json "postinstall"),
// or manually with `npm run sync:basecoat` after upgrading basecoat-css.

import { cp, mkdir, copyFile } from "node:fs/promises";
import { existsSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(__dirname, "..");

const basecoatPkg = path.join(root, "node_modules", "basecoat-css");

if (!existsSync(basecoatPkg)) {
  console.error(
    "basecoat-css not found in node_modules. Run `npm install` first."
  );
  process.exit(1);
}

// 1. Jinja macros
const jinjaSrc = path.join(basecoatPkg, "templates", "jinja");
const jinjaDest = path.join(root, "app", "templates", "basecoat");
await mkdir(jinjaDest, { recursive: true });
await cp(jinjaSrc, jinjaDest, { recursive: true });
console.log(`Copied Jinja macros -> ${path.relative(root, jinjaDest)}`);

// 2. JS runtime (all-in-one bundle, simplest setup)
const jsSrc = path.join(basecoatPkg, "dist", "js", "all.min.js");
const jsDestDir = path.join(root, "app", "static", "js", "vendor");
await mkdir(jsDestDir, { recursive: true });
const jsDest = path.join(jsDestDir, "basecoat.all.min.js");
await copyFile(jsSrc, jsDest);
console.log(`Copied Basecoat JS -> ${path.relative(root, jsDest)}`);

// 3. Basecoat CSS (CDN bundle = self-contained, no sub-imports)
//    Placed alongside input.css so Tailwind CLI can @import it.
const cssSrc  = path.join(basecoatPkg, "dist", "basecoat-vega.cdn.css");
const cssDest = path.join(root, "app", "static", "css", "basecoat-vega.css");
await copyFile(cssSrc, cssDest);
console.log(`Copied Basecoat CSS -> ${path.relative(root, cssDest)}`);

console.log("Basecoat sync complete.");
