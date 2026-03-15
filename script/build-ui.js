const fs = require("fs");
const path = require("path");
const {
  commandSucceeds,
  ensureDir,
  ensureEmptyDir,
  ensureFile,
  parseArgs,
  resolvePythonExe,
  run,
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
const appIcon = path.join(rootDir, "封面", "封面.ico");

function hasModule(moduleName) {
  const script = [
    "import importlib.util, sys",
    `sys.exit(0 if importlib.util.find_spec(${JSON.stringify(moduleName)}) else 1)`,
  ].join("; ");
  return commandSucceeds(pythonExe, ["-c", script], { cwd: rootDir });
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
ensureFile(appIcon, "application icon");

ensureModule("PySide6", "PySide6");
ensureModule("shiboken6", "PySide6");
ensureModule("ncmdump", "ncmdump-py");

ensureEmptyDir(distRoot);
ensureEmptyDir(buildRoot);
run(pythonExe, ["-m", "PyInstaller", "--version"], { cwd: rootDir });

const specRoot = path.join(buildRoot, "spec");
ensureEmptyDir(specRoot);

const excludedQtModules = [
  "PySide6.Qt3DAnimation",
  "PySide6.Qt3DCore",
  "PySide6.Qt3DExtras",
  "PySide6.Qt3DInput",
  "PySide6.Qt3DLogic",
  "PySide6.Qt3DRender",
  "PySide6.QtBluetooth",
  "PySide6.QtCharts",
  "PySide6.QtConcurrent",
  "PySide6.QtDataVisualization",
  "PySide6.QtDBus",
  "PySide6.QtDesigner",
  "PySide6.QtHelp",
  "PySide6.QtHttpServer",
  "PySide6.QtLocation",
  "PySide6.QtMultimedia",
  "PySide6.QtMultimediaWidgets",
  "PySide6.QtNetworkAuth",
  "PySide6.QtNfc",
  "PySide6.QtOpenGL",
  "PySide6.QtOpenGLWidgets",
  "PySide6.QtPdf",
  "PySide6.QtPdfWidgets",
  "PySide6.QtPositioning",
  "PySide6.QtQml",
  "PySide6.QtQuick",
  "PySide6.QtQuick3D",
  "PySide6.QtQuickControls2",
  "PySide6.QtQuickWidgets",
  "PySide6.QtRemoteObjects",
  "PySide6.QtScxml",
  "PySide6.QtSensors",
  "PySide6.QtSerialBus",
  "PySide6.QtSerialPort",
  "PySide6.QtSpatialAudio",
  "PySide6.QtSql",
  "PySide6.QtStateMachine",
  "PySide6.QtSvg",
  "PySide6.QtSvgWidgets",
  "PySide6.QtTest",
  "PySide6.QtTextToSpeech",
  "PySide6.QtUiTools",
  "PySide6.QtWebChannel",
  "PySide6.QtWebEngineCore",
  "PySide6.QtWebEngineQuick",
  "PySide6.QtWebEngineWidgets",
  "PySide6.QtWebSockets",
  "PySide6.QtXml",
  "PySide6.QtXmlPatterns",
];

const pyinstallerArgs = [
  "-m",
  "PyInstaller",
  "--noconfirm",
  "--clean",
  "--onedir",
  "--windowed",
  "--icon",
  appIcon,
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
  "frida",
  "--collect-all",
  "ncmdump",
  "--hidden-import",
  "shiboken6",
  "--hidden-import",
  "PySide6.QtCore",
  "--hidden-import",
  "PySide6.QtGui",
  "--hidden-import",
  "PySide6.QtWidgets",
  "--add-data",
  `${assetsDir};assets`,
  "--add-data",
  `${path.dirname(appIcon)};封面`,
  "--add-data",
  `${kuwoRuntimeDir};src/Infrastructure/platforms/kuwo/runtime_m`,
  mainPy,
];

for (const moduleName of excludedQtModules) {
  pyinstallerArgs.push("--exclude-module", moduleName);
}

run(pythonExe, pyinstallerArgs, { cwd: rootDir });
ensureFile(path.join(distRoot, appName, `${appName}.exe`), "ui onedir executable");
