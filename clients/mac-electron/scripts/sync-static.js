/**
 * 将服务端 app/static 同步到本目录 static/，供 Electron 打包与本地加载。
 */
const fs = require("fs");
const path = require("path");

const root = path.resolve(__dirname, "..", "..", "..");
const src = path.join(root, "app", "static");
const dst = path.join(__dirname, "..", "static");

if (!fs.existsSync(src)) {
  console.error("未找到服务端静态目录:", src);
  process.exit(1);
}
fs.rmSync(dst, { recursive: true, force: true });
fs.cpSync(src, dst, { recursive: true });
console.log("已同步", src, "->", dst);
