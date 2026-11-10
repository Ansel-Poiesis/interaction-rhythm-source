const fs = require("node:fs");
const path = require("node:path");

const root = path.resolve(__dirname, "..");
const htmlPath = path.join(root, "static", "index.html");
const html = fs.readFileSync(htmlPath, "utf8");

// 1) 内联脚本（如保留的轻量片段）逐段做语法检查
const inline = [...html.matchAll(/<script(?![^>]*\bsrc=)[^>]*>([\s\S]*?)<\/script>/gi)]
  .map((match) => match[1])
  .filter((body) => body.trim().length > 0);

inline.forEach((body, index) => {
  try {
    new Function(body);
  } catch (error) {
    error.message = `Inline script ${index + 1}: ${error.message}`;
    throw error;
  }
});

// 2) 外部脚本（static/ 下的 .js，含 i18n.js / app.js）逐文件做语法检查
const external = [...html.matchAll(/<script src="\/([^"]+\.js)"><\/script>/gi)].map((m) => m[1]);
if (external.length === 0) {
  throw new Error("No external scripts referenced in index.html (expected i18n.js / app.js)");
}

for (const rel of external) {
  const target = path.join(root, "static", rel);
  if (!fs.existsSync(target)) {
    throw new Error(`Missing external script: ${target}`);
  }
  try {
    new Function(fs.readFileSync(target, "utf8"));
  } catch (error) {
    error.message = `${rel}: ${error.message}`;
    throw error;
  }
}

console.log(
  `JS_SYNTAX_OK (${inline.length} inline + ${external.length} external script${inline.length + external.length === 1 ? "" : "s"})`
);
