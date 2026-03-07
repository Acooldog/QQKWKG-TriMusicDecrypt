const fs = require("fs");
const path = require("path");
const { spawnSync } = require("child_process");

const rootDir = path.resolve(__dirname, "..");
const distDir = path.join(rootDir, "dist");
const buildDir = path.join(rootDir, "build");
const appName = "QKKDecrypt";
const pythonExe = path.join(rootDir, ".venv", "Scripts", "python.exe");
const mainPy = path.join(rootDir, "main.py");
const assetsDir = path.join(rootDir, "assets");
const kuwoRuntimeDir = path.join(rootDir, "src", "Infrastructure", "platforms", "kuwo", "runtime_m");

function fail(message) {
  console.error(message);
  process.exit(1);
}

function ensureFile(filePath, label) {
  if (!fs.existsSync(filePath)) {
    fail(`Missing ${label}: ${filePath}`);
  }
}

function ensureDir(dirPath, label) {
  if (!fs.existsSync(dirPath) || !fs.statSync(dirPath).isDirectory()) {
    fail(`Missing ${label}: ${dirPath}`);
  }
}

function run(command, args, options = {}) {
  const result = spawnSync(command, args, {
    stdio: "inherit",
    cwd: rootDir,
    shell: false,
    ...options,
  });
  if (result.status !== 0) {
    fail(`Command failed: ${command} ${args.join(" ")}`);
  }
}

function ensureExternalRuntimeDirs(appDir) {
  for (const name of ["plugins", "_log", "output"]) {
    fs.mkdirSync(path.join(appDir, name), { recursive: true });
  }
}

ensureFile(pythonExe, "venv python");
ensureFile(mainPy, "main entry");
ensureDir(assetsDir, "assets directory");
ensureDir(kuwoRuntimeDir, "kuwo runtime directory");
ensureFile(path.join(assetsDir, "kugou_key.xz"), "kugou_key.xz");
ensureFile(path.join(assetsDir, "kudog_native.dll"), "kudog_native.dll");
ensureFile(path.join(assetsDir, "ffmpeg-win-x86_64-v7.1.exe"), "bundled ffmpeg");
ensureFile(path.join(kuwoRuntimeDir, "kwm_export_agent.js"), "kwm_export_agent.js");
ensureFile(path.join(kuwoRuntimeDir, "out", "recovered_signature.json"), "kuwo recovered signature");

run(pythonExe, ["-m", "PyInstaller", "--version"]);

const args = [
  "-m",
  "PyInstaller",
  "--noconfirm",
  "--clean",
  "--onedir",
  "--name",
  appName,
  "--contents-directory",
  "_internal",
  "--distpath",
  distDir,
  "--workpath",
  buildDir,
  "--specpath",
  buildDir,
  "--paths",
  rootDir,
  "--collect-submodules",
  "src",
  "--collect-all",
  "frida",
  "--add-data",
  `${assetsDir};assets`,
  "--add-data",
  `${kuwoRuntimeDir};src/Infrastructure/platforms/kuwo/runtime_m`,
  mainPy,
];

run(pythonExe, args);

const appDir = path.join(distDir, appName);
const builtExe = path.join(appDir, `${appName}.exe`);
ensureFile(builtExe, "built executable");
ensureExternalRuntimeDirs(appDir);
console.log(`Build completed: ${builtExe}`);
