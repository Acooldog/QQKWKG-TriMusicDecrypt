const fs = require("fs");
const os = require("os");
const path = require("path");
const { spawnSync } = require("child_process");
const {
  capture,
  cleanDir,
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
const tempUiWorktreeDir = path.join(os.tmpdir(), "A_QKKd_main_ui_worktree");
const pythonExe = resolvePythonExe(rootDir);
const consoleExeName = "QKKDecrypt.exe";
const uiAppName = "QKKDecrypt-UI";
const uiSetupName = "QKKDecrypt-UI-setup";
const appIcon = path.join(rootDir, "封面", "封面.ico");

function currentBranchName(repoDir) {
  return capture("git", ["branch", "--show-current"], { cwd: repoDir }).trim();
}

function localBranchExists(repoDir, branchName) {
  try {
    capture("git", ["show-ref", "--verify", `refs/heads/${branchName}`], { cwd: repoDir });
    return true;
  } catch {
    return false;
  }
}

function remoteBranchExists(repoDir, remoteRef) {
  try {
    capture("git", ["show-ref", "--verify", remoteRef], { cwd: repoDir });
    return true;
  } catch {
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

function findExistingWorktree(branchName) {
  const raw = capture("git", ["worktree", "list", "--porcelain"], { cwd: rootDir });
  const entries = raw.split(/\r?\n\r?\n/).map((block) => block.trim()).filter(Boolean);
  for (const entry of entries) {
    const lines = entry.split(/\r?\n/);
    let worktreePath = "";
    let branchRef = "";
    for (const line of lines) {
      if (line.startsWith("worktree ")) {
        worktreePath = line.slice("worktree ".length).trim();
      } else if (line.startsWith("branch ")) {
        branchRef = line.slice("branch ".length).trim();
      }
    }
    if (branchRef === `refs/heads/${branchName}` && path.resolve(worktreePath) !== path.resolve(rootDir)) {
      return worktreePath;
    }
  }
  return null;
}

function resolveUiWorktree() {
  const existing = findExistingWorktree("main-ui");
  if (existing) {
    return { repoDir: existing, ephemeral: false };
  }
  removeWorktreeIfPresent(tempUiWorktreeDir);
  run("git", ["worktree", "add", "--force", tempUiWorktreeDir, "main-ui"], { cwd: rootDir });
  return { repoDir: tempUiWorktreeDir, ephemeral: true };
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
  run(
    "node",
    [path.join(rootDir, "script", "build-console.js"), "--dist-root", consoleDistDir, "--build-root", consoleBuildDir],
    { cwd: rootDir, env: { ...process.env, QKK_PYTHON_EXE: pythonExe } },
  );
  const builtConsoleExe = path.join(consoleDistDir, consoleExeName);
  ensureFile(builtConsoleExe, "console build");
  fs.copyFileSync(builtConsoleExe, path.join(releaseDir, consoleExeName));
}

function buildUi(uiRepoDir) {
  runNativeBuild(uiRepoDir);
  run(
    "node",
    [path.join(uiRepoDir, "script", "build-ui.js"), "--dist-root", uiDistDir, "--build-root", uiBuildDir],
    { cwd: uiRepoDir, env: { ...process.env, QKK_PYTHON_EXE: pythonExe } },
  );
  const appDir = path.join(uiDistDir, uiAppName);
  ensureDir(appDir, "ui dist app directory");
  return appDir;
}

function compileUiSetup(appDir) {
  const isccExe = locateIscc();
  if (!isccExe) {
    fail("Inno Setup (ISCC.exe) not found. Install Inno Setup 6 before packaging UI.");
  }
  ensureFile(appIcon, "ui setup icon");
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
SetupIconFile=${appIcon.replace(/\\/g, "\\\\")}
UninstallDisplayIcon={app}\\${uiAppName}.exe

[Files]
Source: "${appDir.replace(/\\/g, "\\\\")}\\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion

[Icons]
Name: "{autoprograms}\\QKKDecrypt UI"; Filename: "{app}\\${uiAppName}.exe"; IconFilename: "{app}\\${uiAppName}.exe"
Name: "{autodesktop}\\QKKDecrypt UI"; Filename: "{app}\\${uiAppName}.exe"; Tasks: desktopicon; IconFilename: "{app}\\${uiAppName}.exe"

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Additional tasks:"

[Run]
Filename: "{app}\\${uiAppName}.exe"; Description: "Launch QKKDecrypt UI"; Flags: nowait postinstall skipifsilent
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
  const uiWorktree = resolveUiWorktree();
  const uiAppDir = buildUi(uiWorktree.repoDir);
  compileUiSetup(uiAppDir);
  if (uiWorktree.ephemeral) {
    removeWorktreeIfPresent(uiWorktree.repoDir);
  }

  const finalAssets = fs.readdirSync(releaseDir).sort();
  console.log(`Release assets ready: ${finalAssets.join(", ")}`);
}

main();
