const fs = require("fs");
const path = require("path");
const {
  ensureDir,
  ensureEmptyDir,
  ensureFile,
  parseArgs,
  resolvePythonExe,
  run,
  capture,
} = require("./build-lib");

const args = parseArgs(process.argv.slice(2));
const rootDir = path.resolve(__dirname, "..");
const distRoot = path.resolve(args["dist-root"] || path.join(rootDir, "dist", "ui"));
const buildRoot = path.resolve(args["build-root"] || path.join(rootDir, "build", "ui"));
const appName = "QKKDecrypt-UI";
const pythonExe = resolvePythonExe(rootDir);
const mainPy = path.join(rootDir, "main.py");
const assetsDir = path.join(rootDir, "assets");
const kuwoRuntimeDir = path.join(rootDir, "src", "Infrastructure", "platforms", "kuwo", "runtime_m");

function hasModule(moduleName) {
  const script = [
    "import importlib.util, sys",
    `sys.exit(0 if importlib.util.find_spec(${JSON.stringify(moduleName)}) else 1)`,
  ].join("; ");
  try {
    capture(pythonExe, ["-c", script], { cwd: rootDir });
    return true;
  } catch {
    return false;
  }
}

function ensureModule(moduleName, packageName = moduleName) {
  if (hasModule(moduleName)) {
    return;
  }
  run(pythonExe, ["-m", "pip", "install", packageName], { cwd: rootDir });
  if (!hasModule(moduleName)) {
    throw new Error(`Python module '${moduleName}' is still unavailable after installing '${packageName}'.`);
  }
}

ensureFile(mainPy, "main entry");
ensureDir(assetsDir, "assets directory");
ensureDir(kuwoRuntimeDir, "kuwo runtime directory");
ensureFile(path.join(assetsDir, "kugou_key.xz"), "kugou_key.xz");
ensureFile(path.join(assetsDir, "kudog_native.dll"), "kudog_native.dll");
ensureFile(path.join(assetsDir, "ffmpeg-win-x86_64-v7.1.exe"), "bundled ffmpeg");
ensureFile(path.join(kuwoRuntimeDir, "kwm_export_agent.js"), "kwm_export_agent.js");
ensureFile(path.join(kuwoRuntimeDir, "out", "recovered_signature.json"), "kuwo recovered signature");

ensureModule("PySide6", "PySide6");
ensureModule("shiboken6", "PySide6");

ensureEmptyDir(distRoot);
ensureEmptyDir(buildRoot);
run(pythonExe, ["-m", "PyInstaller", "--version"], { cwd: rootDir });

const specRoot = path.join(buildRoot, "spec");
ensureEmptyDir(specRoot);

const pyinstallerArgs = [
  "-m",
  "PyInstaller",
  "--noconfirm",
  "--clean",
  "--onedir",
  "--windowed",
  "--name",
  appName,
  "--contents-directory",
  "_internal",
  "--distpath",
  distRoot,
  "--workpath",
  path.join(buildRoot, "work"),
  "--specpath",
  specRoot,
  "--paths",
  rootDir,
  "--collect-submodules",
  "src",
  "--collect-all",
  "PySide6",
  "--collect-all",
  "shiboken6",
  "--collect-all",
  "frida",
  "--hidden-import",
  "PySide6.QtCore",
  "--hidden-import",
  "PySide6.QtGui",
  "--hidden-import",
  "PySide6.QtWidgets",
  "--add-data",
  `${assetsDir};assets`,
  "--add-data",
  `${kuwoRuntimeDir};src/Infrastructure/platforms/kuwo/runtime_m`,
  mainPy,
];

run(pythonExe, pyinstallerArgs, { cwd: rootDir });
ensureFile(path.join(distRoot, appName, `${appName}.exe`), "ui onedir executable");
