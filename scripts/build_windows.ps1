<#
.SYNOPSIS
    Build PixelPack.exe with PyInstaller.

.DESCRIPTION
    Produces a self-contained folder at dist\PixelPack\ containing
    PixelPack.exe. The target machine needs no Python installation.

    Directory mode (not onefile) is deliberate: a onefile build unpacks itself
    into %TEMP% on every launch, which is slow for a Qt application and looks
    like suspicious behaviour to some antivirus products.

.PARAMETER Clean
    Delete build\ and dist\ before building.

.PARAMETER Zip
    Also produce dist\PixelPack-<version>-win64.zip for distribution.

.PARAMETER Console
    Keep the console window. Useful for diagnosing a build that will not start.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\build_windows.ps1 -Clean -Zip
#>
[CmdletBinding()]
param(
    [switch]$Clean,
    [switch]$Zip,
    [switch]$Console
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

# Defined up front so the strict-mode checks below can read it before the
# first native command has run.
$LASTEXITCODE = 0

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$SrcDir      = Join-Path $ProjectRoot 'src'
$AssetsDir   = Join-Path $ProjectRoot 'assets'
$IconPath    = Join-Path $AssetsDir 'pixelpack.ico'
# Runtime assets live inside the package so they resolve from a source checkout
# and from a frozen bundle by the same expression (see utils/resources.py).
$PackageAssets = Join-Path $SrcDir 'pixelpack\assets'
$VersionInfo   = Join-Path $AssetsDir 'version_info.txt'
$BuildDir    = Join-Path $ProjectRoot 'build'
$DistDir     = Join-Path $ProjectRoot 'dist'
$AppName     = 'PixelPack'

function Write-Step([string]$Message) {
    Write-Host ''
    Write-Host "==> $Message" -ForegroundColor Cyan
}

function Write-Ok([string]$Message) {
    Write-Host "    $Message" -ForegroundColor Green
}

function Write-Note([string]$Message) {
    Write-Host "    $Message" -ForegroundColor Yellow
}

function Invoke-SelfCheck {
    <# Start the frozen executable and let it build its own UI, then report.
       Returns the captured output; throws if it cannot start. #>
    param(
        [string]$ExePath,
        [string]$Platform = ''
    )

    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = $ExePath
    $psi.Arguments = '--self-check'
    $psi.UseShellExecute = $false
    $psi.RedirectStandardOutput = $true
    $psi.RedirectStandardError = $true
    $psi.CreateNoWindow = $true

    # Python encodes stdout with the system ANSI code page whenever the stream
    # is not a console -- GBK on a Chinese Windows. Decoding that as UTF-8 turns
    # every line into mojibake, so match the code page Python actually used.
    $ansiCodePage = [System.Globalization.CultureInfo]::CurrentCulture.TextInfo.ANSICodePage
    $psi.StandardOutputEncoding = [System.Text.Encoding]::GetEncoding($ansiCodePage)
    $psi.StandardErrorEncoding = [System.Text.Encoding]::GetEncoding($ansiCodePage)

    if ($Platform) {
        $psi.EnvironmentVariables['QT_QPA_PLATFORM'] = $Platform
    }

    $probe = [System.Diagnostics.Process]::Start($psi)
    # Read while the child runs: waiting first can deadlock once a pipe fills.
    $stdoutTask = $probe.StandardOutput.ReadToEndAsync()
    $stderrTask = $probe.StandardError.ReadToEndAsync()

    if (-not $probe.WaitForExit(60000)) {
        $probe.Kill()
        throw "启动自检超时（平台：$Platform）：$ExePath 无法正常启动。"
    }

    $out = $stdoutTask.Result
    $err = $stderrTask.Result

    if ($probe.ExitCode -ne 0) {
        Write-Note "自检返回码 $($probe.ExitCode)（平台：$Platform）"
        if ($err) {
            $err.Trim().Split("`n") |
                Select-Object -First 20 | ForEach-Object { Write-Note $_.TrimEnd() }
        }
        throw "可执行文件已经生成，但启动失败（平台：$Platform）。请用 -Console 重新构建以查看完整输出。"
    }

    return [pscustomobject]@{ Output = $out; Errors = $err }
}

function Test-PythonModule {
    <# Ask Python itself whether a module is importable. Using find_spec means
       a missing module produces a clean exit code instead of a traceback on
       stderr, which PowerShell 5.1 would wrap in an ErrorRecord. #>
    param([string]$Module)

    # $null = ... keeps the probe's stdout out of the function's return value,
    # which would otherwise make the caller see an array instead of a boolean.
    $null = & $Python -c "import importlib.util, sys; sys.exit(0 if importlib.util.find_spec('$Module') else 1)"
    return $LASTEXITCODE -eq 0
}

# ---------------------------------------------------------------- interpreter
Write-Step 'Locating the Python interpreter'

$Python = $null
$VenvPython = Join-Path $ProjectRoot '.venv\Scripts\python.exe'
if (Test-Path $VenvPython) {
    $Python = $VenvPython
    Write-Ok "使用虚拟环境：$Python"
} else {
    $candidate = Get-Command python -ErrorAction SilentlyContinue
    if ($candidate) {
        $Python = $candidate.Source
        Write-Note "未找到 .venv，回退到系统 Python：$Python"
    }
}

if (-not $Python) {
    throw '找不到 Python。请先创建虚拟环境：python -m venv .venv'
}

$Version = & $Python -c "import sys; print('{0}.{1}'.format(*sys.version_info[:2]))"
Write-Ok "Python 版本：$Version"
if ([version]$Version -lt [version]'3.12') {
    throw "PixelPack 需要 Python 3.12 或更高版本，当前为 $Version。"
}

# ------------------------------------------------------------------ packages
Write-Step 'Checking build dependencies'

if (-not (Test-PythonModule 'PyInstaller')) {
    Write-Note 'PyInstaller 未安装，正在安装…'
    & $Python -m pip install --upgrade 'pyinstaller>=6.0'
    if ($LASTEXITCODE -ne 0) { throw 'PyInstaller 安装失败。' }
}
$PyInstallerVersion = & $Python -c "import PyInstaller; print(PyInstaller.__version__)"
Write-Ok "PyInstaller $PyInstallerVersion"

# The package lives under src\, so put it on the path for the import probe.
$previousPythonPath = $env:PYTHONPATH
$env:PYTHONPATH = $SrcDir
try {
    foreach ($module in @('PySide6', 'PIL', 'pixelpack')) {
        if (-not (Test-PythonModule $module)) {
            throw "缺少依赖模块：$module。请先运行 pip install -e `".[dev]`""
        }
        Write-Ok "已安装：$module"
    }
    $AppVersion = & $Python -c "import pixelpack; print(pixelpack.__version__)"
} finally {
    if ($previousPythonPath) {
        $env:PYTHONPATH = $previousPythonPath
    } else {
        Remove-Item Env:\PYTHONPATH -ErrorAction SilentlyContinue
    }
}

# ---------------------------------------------------------------------- icon
Write-Step 'Preparing the application icon'

if (-not (Test-Path $IconPath)) {
    Write-Note '图标不存在，正在生成…'
    & $Python (Join-Path $PSScriptRoot 'make_icon.py')
    if ($LASTEXITCODE -ne 0) { throw '图标生成失败。' }
}
if (-not (Test-Path $IconPath)) {
    throw "图标文件缺失：$IconPath"
}
Write-Ok "图标：$IconPath"

# ------------------------------------------------------------- version info
Write-Step 'Writing the Windows version resource'

# Generated rather than checked in, so the EXE properties are read from the
# same pixelpack constants the About dialog shows. Runs every build: a stale
# resource would be a silent lie in the file's Properties page.
& $Python (Join-Path $PSScriptRoot 'make_version_info.py')
if ($LASTEXITCODE -ne 0) { throw '版本信息生成失败。' }
if (-not (Test-Path $VersionInfo)) {
    throw "版本信息文件缺失：$VersionInfo"
}
Write-Ok "版本信息：$VersionInfo"

# --------------------------------------------------------------------- clean
if ($Clean) {
    Write-Step 'Cleaning previous output'
    foreach ($dir in @($BuildDir, $DistDir)) {
        if (Test-Path $dir) {
            Remove-Item -Recurse -Force $dir
            Write-Ok "已删除 $dir"
        }
    }
}

# --------------------------------------------------------------------- build
Write-Step 'Running PyInstaller'
Write-Ok "PixelPack $AppVersion"

$arguments = @(
    '--noconfirm',
    '--clean',
    '--name', $AppName,
    '--icon', $IconPath,
    '--version-file', $VersionInfo,
    '--paths', $SrcDir,
    '--distpath', $DistDir,
    '--workpath', $BuildDir,
    '--specpath', $BuildDir,
    '--add-data', "$IconPath;assets",
    # The About dialog's logo. The destination mirrors the package tree, which
    # is what makes utils/resources.py work unchanged when frozen.
    '--add-data', "$PackageAssets;pixelpack/assets",
    '--onedir',
    # Trim the Qt modules a widget-only application never touches.
    '--exclude-module', 'PySide6.QtQml',
    '--exclude-module', 'PySide6.QtQuick',
    '--exclude-module', 'PySide6.QtQuick3D',
    '--exclude-module', 'PySide6.QtQuickWidgets',
    '--exclude-module', 'PySide6.QtWebEngineCore',
    '--exclude-module', 'PySide6.QtWebEngineWidgets',
    '--exclude-module', 'PySide6.QtWebEngineQuick',
    '--exclude-module', 'PySide6.QtWebChannel',
    '--exclude-module', 'PySide6.QtMultimedia',
    '--exclude-module', 'PySide6.QtMultimediaWidgets',
    '--exclude-module', 'PySide6.Qt3DCore',
    '--exclude-module', 'PySide6.Qt3DRender',
    '--exclude-module', 'PySide6.QtCharts',
    '--exclude-module', 'PySide6.QtDataVisualization',
    '--exclude-module', 'PySide6.QtBluetooth',
    '--exclude-module', 'PySide6.QtNfc',
    '--exclude-module', 'PySide6.QtPositioning',
    '--exclude-module', 'PySide6.QtSerialPort',
    '--exclude-module', 'PySide6.QtSql',
    '--exclude-module', 'PySide6.QtTest',
    '--exclude-module', 'PySide6.QtDesigner',
    '--exclude-module', 'PySide6.QtHelp',
    '--exclude-module', 'PySide6.QtOpenGL',
    '--exclude-module', 'PySide6.QtPdf',
    '--exclude-module', 'PySide6.QtSensors',
    '--exclude-module', 'PySide6.QtSvgWidgets',
    '--exclude-module', 'PySide6.QtRemoteObjects',
    '--exclude-module', 'PySide6.QtScxml',
    '--exclude-module', 'PySide6.QtStateMachine',
    '--exclude-module', 'PySide6.QtTextToSpeech',
    '--exclude-module', 'PySide6.QtUiTools',
    '--exclude-module', 'tkinter',
    '--exclude-module', 'unittest',
    '--exclude-module', 'pytest',
    '--exclude-module', 'numpy',
    '--exclude-module', 'setuptools',
    '--exclude-module', 'pip',
    # AVIF is not one of the formats PixelPack handles, and Pillow's codec is
    # the single largest thing in the bundle after Qt.
    '--exclude-module', 'PIL.AvifImagePlugin'
)

if ($Console) {
    $arguments += '--console'
} else {
    $arguments += '--windowed'
}

$arguments += (Join-Path $PSScriptRoot 'entry_point.py')

& $Python -m PyInstaller @arguments
if ($LASTEXITCODE -ne 0) { throw 'PyInstaller 构建失败。' }

# ------------------------------------------------------- trim, then verify
Write-Step 'Trimming unused payload'

$AppDir = Join-Path $DistDir $AppName
$InternalDir = Join-Path $AppDir '_internal'

# PyInstaller's PySide6 hook collects Qt's QML/Quick stack, the virtual
# keyboard and Qt's own translation catalogues no matter what the application
# imports. PixelPack is a QWidget application: it never touches QML, so all of
# this is dead weight. The startup self-check below runs afterwards, which is
# what proves nothing removed here was actually needed.
$DeadWeight = @(
    'PySide6\Qt6Quick.dll'
    'PySide6\Qt6Qml.dll'
    'PySide6\Qt6QmlModels.dll'
    'PySide6\Qt6QmlMeta.dll'
    'PySide6\Qt6QmlWorkerScript.dll'
    'PySide6\Qt6VirtualKeyboard.dll'      # links the QML stack above
    'PySide6\Qt6Pdf.dll'
    'PySide6\plugins\imageformats\qpdf.dll'
    'PySide6\plugins\platforminputcontexts'
    'PySide6\translations'
    'PIL\_avif.cp313-win_amd64.pyd'
)

$saved = 0
foreach ($relative in $DeadWeight) {
    $stale = Join-Path $InternalDir $relative
    if (-not (Test-Path $stale)) { continue }
    $bytes = (Get-ChildItem $stale -Recurse -File | Measure-Object -Property Length -Sum).Sum
    Remove-Item -Recurse -Force $stale
    $saved += $bytes
    Write-Ok ("已移除 {0}" -f $relative)
}
Write-Ok ("共减少 {0:N1} MB" -f ($saved / 1MB))

Write-Step 'Verifying the build'

$ExePath = Join-Path $AppDir "$AppName.exe"
if (-not (Test-Path $ExePath)) {
    throw "构建完成但找不到可执行文件：$ExePath"
}

# Read the resource back off the produced binary rather than trusting the file
# that was handed to PyInstaller: this is the value Explorer will show.
$ExeInfo = (Get-Item $ExePath).VersionInfo
Write-Ok "公司名称：$($ExeInfo.CompanyName)"
Write-Ok "文件版本：$($ExeInfo.FileVersion)"
Write-Ok "版权信息：$($ExeInfo.LegalCopyright)"

if ($ExeInfo.CompanyName -ne 'HMYS Tech') {
    throw "EXE 元数据中的 CompanyName 应为 'HMYS Tech'，实际为 '$($ExeInfo.CompanyName)'。"
}
if ($ExeInfo.FileVersion -notlike "$AppVersion*") {
    throw "EXE 元数据中的 FileVersion 应为 $AppVersion，实际为 '$($ExeInfo.FileVersion)'。"
}
if ($ExeInfo.LegalCopyright -notlike '*HMYS Tech*') {
    throw "EXE 元数据中的 LegalCopyright 不正确：'$($ExeInfo.LegalCopyright)'。"
}
Write-Ok 'EXE 元数据校验通过'

$ExeSize = (Get-Item $ExePath).Length
$TotalSize = (Get-ChildItem $AppDir -Recurse -File |
              Measure-Object -Property Length -Sum).Sum

Write-Ok ("可执行文件：{0} ({1:N2} MB)" -f $ExePath, ($ExeSize / 1MB))
Write-Ok ("完整目录：{0:N1} MB" -f ($TotalSize / 1MB))

# A frozen build that cannot construct its own widgets is the failure mode
# worth catching here, so actually start it. Two passes on purpose: the
# offscreen one proves the widgets, stylesheet and result dialog build, and the
# native one proves the Windows platform plugin survived the trim above.
# Neither puts a window on screen -- --self-check returns before show().
Write-Host ''
Write-Host '    正在做启动自检…' -ForegroundColor Gray

$offscreen = Invoke-SelfCheck -ExePath $ExePath -Platform 'offscreen'
$offscreen.Output.Trim().Split("`n") | ForEach-Object { Write-Ok $_.TrimEnd() }
Write-Ok '离屏平台自检通过'

$native = Invoke-SelfCheck -ExePath $ExePath
Write-Ok '本机平台自检通过'

# PixelPack logs to stderr by design, so skip its own records and repeat
# anything else -- that is where Qt puts its warnings.
$noise = @(
    ($offscreen.Errors + "`n" + $native.Errors).Split("`n") |
        Where-Object { $_ -and ($_ -notmatch '^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3} \[') }
)
if ($noise.Count -gt 0) {
    Write-Note '启动时另有输出：'
    $noise | Select-Object -First 10 | ForEach-Object { Write-Note $_.TrimEnd() }
}

# ----------------------------------------------------------------------- zip
if ($Zip) {
    Write-Step 'Packaging for distribution'
    $ZipPath = Join-Path $DistDir "$AppName-$AppVersion-win64.zip"
    if (Test-Path $ZipPath) { Remove-Item -Force $ZipPath }
    Compress-Archive -Path $AppDir -DestinationPath $ZipPath
    Write-Ok ("已生成 {0} ({1:N1} MB)" -f $ZipPath, ((Get-Item $ZipPath).Length / 1MB))
}

# --------------------------------------------------------------------- done
Write-Host ''
Write-Host '构建完成。' -ForegroundColor Green
Write-Host "  运行：$ExePath"
Write-Host "  分发：把整个 $AppDir 文件夹复制给对方即可，无需安装 Python。"
Write-Host ''
