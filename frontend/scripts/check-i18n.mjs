import fs from "node:fs";
import ts from "typescript";

const sources = ["src/main.tsx", "src/DocumentAccess.tsx", "src/format.ts", "src/api.ts"];
const i18nSource = fs.readFileSync("src/i18n.ts", "utf8");
const i18nFile = ts.createSourceFile("src/i18n.ts", i18nSource, ts.ScriptTarget.Latest, true);
const rules = [];

function collectRules(node) {
  if (
    ts.isArrayLiteralExpression(node) &&
    node.elements.length === 3 &&
    node.elements.every((item) => ts.isStringLiteralLike(item))
  ) {
    rules.push(node.elements.map((item) => item.text));
  }
  ts.forEachChild(node, collectRules);
}
collectRules(i18nFile);

const sortedRules = rules.sort((a, b) => b[0].length - a[0].length);
const untranslated = [];
const allowedRussian = new Set(["Г"]); // Initial used by the synthetic demo profile avatar.

function translated(value, index) {
  let result = value.trim().replace(/\s+/g, " ");
  for (const rule of sortedRules) result = result.replaceAll(rule[0], rule[index]);
  return result;
}

for (const path of sources) {
  const source = fs.readFileSync(path, "utf8");
  const file = ts.createSourceFile(path, source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
  function visit(node) {
    const isText = ts.isStringLiteralLike(node) || ts.isJsxText(node) ||
      node.kind === ts.SyntaxKind.TemplateHead || node.kind === ts.SyntaxKind.TemplateMiddle ||
      node.kind === ts.SyntaxKind.TemplateTail;
    if (isText) {
      const value = node.text.trim();
      if (/[А-Яа-яЁё]/.test(value) && !allowedRussian.has(value)) {
        for (const [locale, index] of [["me", 1], ["en", 2]]) {
          const result = translated(value, index);
          if (/[А-Яа-яЁё]/.test(result)) {
            const line = file.getLineAndCharacterOfPosition(node.getStart()).line + 1;
            untranslated.push(`${path}:${line} [${locale}] ${JSON.stringify(value)} -> ${JSON.stringify(result)}`);
          }
        }
      }
    }
    ts.forEachChild(node, visit);
  }
  visit(file);
}

if (untranslated.length) {
  console.error("Untranslated interface text:\n" + untranslated.join("\n"));
  process.exit(1);
}
console.log(`i18n audit passed: ${sources.length} source files, RU/CG/EN`);
