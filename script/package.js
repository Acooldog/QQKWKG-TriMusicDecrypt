const fs = require("fs");
const os = require("os");
const path = require("path");
const { spawnSync } = require("child_process");
const {
  capture,
  cleanDir,
  copyRecursive,
  ensureDir,
  ensureEmptyDir,
  ensureFile,
  fail,
  locateIscc,
  resolvePythonExe,
  run,
} = require("./build-lib");

const rootDir = path.resolve(__dirname, "..");
const packageJson = JSON.parse(fs.readFileSync(path.join(rootDir, "package.json"), "utf8"));
const releaseDir = path.join(rootDir, "release");
const distDir = path.join(rootDir, "dist");
const buildDir = path.join(rootDir, "build");
const uiDistDir = path.join(distDir, "ui");
const uiBuildDir = path.join(buildDir, "ui");
const consoleDistDir = path.join(distDir, "console");
const consoleBuildDir = path.join(buildDir, "console");
const uiWorktreeDir = path.join(os.tmpdir(), "A_QKKd_main_ui_worktree");
const pythonExe = resolvePythonExe(rootDir);
const consoleExeName = "QKKDecrypt.exe";
const uiAppName = "QKKDecrypt-UI";
const uiSetupName = "QKKDecrypt-UI-setup";

function currentBranchName(repoDir) {
  return capture("git", ["branch", "--show-current"], { cwd: repoDir }).trim();
}

function localBranchExists(repoDir, branchName) {
  try {
    capture("git", ["show-ref", "--verify", `refs/heads/${branchName}`], { cwd: repoDir });
    return true;
  } catch (error) {
    return false;
  }
}

function remoteBranchExists(repoDir, remoteRef) {
  try {
    capture("git", ["show-ref", "--verify", remoteRef], { cwd: repoDir });
    return true;
  } catch (error) {
    return false;
  }
}

function ensureMainUiBranch() {
  if (localBranchExists(rootDir, "main-ui")) {
    return;
  }
  const remoteCandidates = [
    "refs/remotes/origin/main-ui",
    "refs/remotes/githubQKK/main-ui",
    "refs/remotes/giteeQKK/main-ui",
  ];
  for (const remoteRef of remoteCandidates) {
    if (remoteBranchExists(rootDir, remoteRef)) {
      run("git", ["branch", "main-ui", remoteRef], { cwd: rootDir });
      return;
    }
  }
  fail("Local branch 'main-ui' not found. Create and commit the UI branch before packaging.");
}

function removeWorktreeIfPresent(worktreePath) {
  spawnSync("git", ["worktree", "remove", "--force", worktreePath], {
    cwd: rootDir,
    stdio: "ignore",
    shell: false,
  });
  cleanDir(worktreePath);
}

function addUiWorktree() {
  removeWorktreeIfPresent(uiWorktreeDir);
  run("git", ["worktree", "add", "--force", uiWorktreeDir, "main-ui"], { cwd: rootDir });
}

function runNativeBuild(repoDir) {
  run(
    "powershell",
    ["-ExecutionPolicy", "Bypass", "-File", path.join(repoDir, "native", "build_native.ps1")],
    {
      cwd: repoDir,
      env: { ...process.env },
    },
  );
}

function buildConsole() {
  runNativeBuild(rootDir);
  run("node", [path.join(rootDir, "script", "build-console.js"), "--dist-root", consoleDistDir, "--build-root", consoleBuildDir], {
    cwd: rootDir,
    env: { ...process.env, QKK_PYTHON_EXE: pythonExe },
  });
  const builtConsoleExe = path.join(consoleDistDir, consoleExeName);
  ensureFile(builtConsoleExe, "console build");
  fs.copyFileSync(builtConsoleExe, path.join(releaseDir, consoleExeName));
}

function buildUi() {
  addUiWorktree();
  runNativeBuild(uiWorktreeDir);
  run("node", [path.join(uiWorktreeDir, "script", "build-ui.js"), "--dist-root", uiDistDir, "--build-root", uiBuildDir], {
    cwd: uiWorktreeDir,
    env: { ...process.env, QKK_PYTHON_EXE: pythonExe },
  });
  const appDir = path.join(uiDistDir, uiAppName);
  ensureDir(appDir, "ui dist app directory");
  return appDir;
}

function compileUiSetup(appDir) {
  const isccExe = locateIscc();
  if (!isccExe) {
    fail("Inno Setup (ISCC.exe) not found. Install Inno Setup 6 before packaging UI.");
  }
  const scriptPath = path.join(buildDir, "ui-installer.iss");
  const iss = `
[Setup]
AppId={{DFB7B7A5-50CE-4B9A-9A61-84C42EECAD0E}}
AppName=QKKDecrypt UI
AppVersion=${packageJson.version}
AppPublisher=Acooldog
DefaultDirName={autopf}\\QKKDecrypt UI
DefaultGroupName=QKKDecrypt UI
DisableProgramGroupPage=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=${releaseDir.replace(/\\/g, "\\\\")}
OutputBaseFilename=${uiSetupName}
Compression=lzma
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=lowest

[Files]
Source: "${appDir.replace(/\\/g, "\\\\")}\\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion

[Icons]
Name: "{autoprograms}\\QKKDecrypt UI"; Filename: "{app}\\${uiAppName}.exe"
Name: "{autodesktop}\\QKKDecrypt UI"; Filename: "{app}\\${uiAppName}.exe"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "附加任务:"

[Run]
Filename: "{app}\\${uiAppName}.exe"; Description: "启动 QKKDecrypt UI"; Flags: nowait postinstall skipifsilent
`.trim();
  fs.mkdirSync(path.dirname(scriptPath), { recursive: true });
  fs.writeFileSync(scriptPath, iss, "utf8");
  run(isccExe, [scriptPath], { cwd: rootDir });
  ensureFile(path.join(releaseDir, `${uiSetupName}.exe`), "ui setup");
}

function main() {
  if (currentBranchName(rootDir) !== "main") {
    fail("Packaging must be run from the 'main' branch.");
  }
  ensureMainUiBranch();
  ensureEmptyDir(releaseDir);
  fs.mkdirSync(distDir, { recursive: true });
  fs.mkdirSync(buildDir, { recursive: true });

  buildConsole();
  const uiAppDir = buildUi();
  compileUiSetup(uiAppDir);
  removeWorktreeIfPresent(uiWorktreeDir);

  const finalAssets = fs.readdirSync(releaseDir).sort();
  console.log(`Release assets ready: ${finalAssets.join(", ")}`);
}

main();
