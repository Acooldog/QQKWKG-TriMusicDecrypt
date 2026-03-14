const path = require("path");
const {
  capture,
  ensureDir,
  ensureEmptyDir,
  ensureFile,
  parseArgs,
  resolvePythonExe,
  run,
} = require("./build-lib");

const args = parseArgs(process.argv.slice(2));
const rootDir = path.resolve(__dirname, "..");
const distRoot = path.resolve(args["dist-root"] || path.join(rootDir, "dist", "console"));
const buildRoot = path.resolve(args["build-root"] || path.join(rootDir, "build", "console"));
const appName = "QKKDecrypt";
const pythonExe = resolvePythonExe(rootDir);
const mainPy = path.join(rootDir, "main.py");
const assetsDir = path.join(rootDir, "assets");
const kuwoRuntimeDir = path.join(rootDir, "src", "Infrastructure", "platforms", "kuwo", "runtime_m");
const appIcon = path.join(rootDir, "封面", "封面.ico");

ensureFile(mainPy, "main entry");
ensureDir(assetsDir, "assets directory");
ensureDir(kuwoRuntimeDir, "kuwo runtime directory");
ensureFile(path.join(assetsDir, "kugou_key.xz"), "kugou_key.xz");
ensureFile(path.join(assetsDir, "kudog_native.dll"), "kudog_native.dll");
ensureFile(path.join(assetsDir, "ffmpeg-win-x86_64-v7.1.exe"), "bundled ffmpeg");
ensureFile(path.join(kuwoRuntimeDir, "kwm_export_agent.js"), "kwm_export_agent.js");
ensureFile(path.join(kuwoRuntimeDir, "out", "recovered_signature.json"), "kuwo recovered signature");
ensureFile(appIcon, "application icon");

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

ensureModule("ncmdump", "ncmdump-py");

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
  "--onefile",
  "--icon",
  appIcon,
  "--name",
  appName,
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
  "frida",
  "--collect-all",
  "ncmdump",
  "--add-data",
  `${assetsDir};assets`,
  "--add-data",
  `${path.dirname(appIcon)};封面`,
  "--add-data",
  `${kuwoRuntimeDir};src/Infrastructure/platforms/kuwo/runtime_m`,
  mainPy,
];

run(pythonExe, pyinstallerArgs, { cwd: rootDir });
ensureFile(path.join(distRoot, `${appName}.exe`), "console onefile executable");
