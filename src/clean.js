// clean.js
const fs = require("fs");
const path = require("path");

// Directory to clean
const distDir = path.join(__dirname, "dist");

// Remove dist directory if it exists
if (fs.existsSync(distDir)) {
  fs.rmSync(distDir, { recursive: true, force: true });
  console.log("✓ Cleaned dist directory");
} else {
  console.log("✓ No dist directory to clean");
}

// Remove any .js files from src (in case of accidental compilation)
function cleanJsFiles(dir) {
  const files = fs.readdirSync(dir);

  files.forEach((file) => {
    const fullPath = path.join(dir, file);
    const stat = fs.statSync(fullPath);

    if (stat.isDirectory()) {
      cleanJsFiles(fullPath);
    } else if (path.extname(file) === ".js" && !file.includes(".config.")) {
      fs.unlinkSync(fullPath);
      console.log(`✓ Removed ${fullPath}`);
    }
  });
}

const srcDir = path.join(__dirname, "src");
if (fs.existsSync(srcDir)) {
  cleanJsFiles(srcDir);
}

console.log("✓ Clean complete");
