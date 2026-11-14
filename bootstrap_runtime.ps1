$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"
try { chcp 65001 | Out-Null } catch { }
try { [Console]::OutputEncoding = New-Object System.Text.UTF8Encoding $false } catch { }
try { [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12 } catch { }

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$runtime = Join-Path $root "pyruntime"
$python = Join-Path $runtime "python.exe"
$pythonw = Join-Path $runtime "pythonw.exe"
$sitePackages = Join-Path $runtime "Lib\site-packages"
$pyside = Join-Path $sitePackages "PySide6"

function Step($message) { Write-Host "[Darkitten] $message" }

if ((Test-Path $python) -and (Test-Path $pythonw) -and (Test-Path $pyside)) {
    Step "私有运行时已就绪。"
    exit 0
}

$pyVersion = "3.12.10"
$zipName = "python-$pyVersion-embed-amd64.zip"
$sources = @(
    "https://registry.npmmirror.com/-/binary/python/$pyVersion/$zipName",
    "https://mirrors.huaweicloud.com/python/$pyVersion/$zipName",
    "https://www.python.org/ftp/python/$pyVersion/$zipName"
)
$indexes = @(
    "https://mirrors.aliyun.com/pypi/simple/",
    "https://repo.huaweicloud.com/repository/pypi/simple/"
)

$zipPath = Join-Path $env:TEMP "darkitten-$zipName"
$downloaded = $false
foreach ($url in $sources) {
    Step "下载 Python $pyVersion 运行时：$url"
    try {
        Invoke-WebRequest -UseBasicParsing -Uri $url -OutFile $zipPath -TimeoutSec 300
        if ((Get-Item $zipPath).Length -gt 5000000) { $downloaded = $true; break }
        Step "文件不完整，换下一个镜像。"
    } catch {
        Step "镜像不可用：$($_.Exception.Message)"
    }
}
if (-not $downloaded) {
    Step "Python 运行时下载失败。请检查网络后重试。"
    exit 1
}

try {
    if (Test-Path $runtime) { Remove-Item -Recurse -Force $runtime }
    New-Item -ItemType Directory -Path $runtime | Out-Null
    Expand-Archive -Path $zipPath -DestinationPath $runtime -Force
} catch {
    Step "解压失败：$($_.Exception.Message)"
    exit 1
} finally {
    Remove-Item $zipPath -Force -ErrorAction SilentlyContinue
}

# 让嵌入式 Python 加载 site-packages。
$pth = Get-ChildItem $runtime -Filter "python*._pth" | Select-Object -First 1
if (-not $pth) {
    Step "运行时缺少 python._pth，无法继续。"
    exit 1
}
$lines = @(Get-Content $pth.FullName)
$lines = $lines | ForEach-Object {
    if ($_ -match "^\s*#\s*import\s+site\s*$") { "import site" } else { $_ }
}
if (-not ($lines -contains "Lib\site-packages")) { $lines += "Lib\site-packages" }
Set-Content -Path $pth.FullName -Value $lines -Encoding ASCII

# 用 pip wheel 自举，不依赖 get-pip.py。
$pipWheel = Join-Path $env:TEMP "darkitten-pip.whl"
$pipReady = $false
foreach ($index in $indexes) {
    Step "获取 pip：$index"
    try {
        $html = (Invoke-WebRequest -UseBasicParsing -Uri ($index + "pip/") -TimeoutSec 90).Content
        $candidates = [regex]::Matches($html, 'href="([^"]*pip-(\d+\.\d+(?:\.\d+)?)-py3-none-any\.whl[^"]*)"')
        if ($candidates.Count -gt 0) {
            $best = $candidates | Sort-Object { [version]($_.Groups[2].Value) } | Select-Object -Last 1
            $relative = $best.Groups[1].Value.Split("#")[0]
            $baseUri = [Uri]($index + "pip/")
            $wheelUrl = [Uri]::new($baseUri, $relative).AbsoluteUri
            Invoke-WebRequest -UseBasicParsing -Uri $wheelUrl -OutFile $pipWheel -TimeoutSec 180
            if ((Get-Item $pipWheel).Length -gt 500000) { $pipReady = $true; break }
        }
    } catch {
        Step "镜像不可用：$($_.Exception.Message)"
    }
}
if (-not $pipReady) {
    Step "pip 下载失败。请检查网络后重试。"
    exit 1
}

$installed = $false
foreach ($index in $indexes) {
    Step "安装 PySide6-Essentials==6.12.0：$index"
    & $python "$pipWheel\pip" install --no-cache-dir --disable-pip-version-check --no-warn-script-location -i $index "PySide6-Essentials==6.12.0"
    if ($LASTEXITCODE -eq 0) { $installed = $true; break }
    Step "该镜像安装失败，换下一个镜像。"
}
Remove-Item $pipWheel -Force -ErrorAction SilentlyContinue

if (-not $installed) {
    Step "PySide6 安装失败。请检查网络后重试。"
    exit 1
}

& $python -c "import PySide6" 2>$null
if ($LASTEXITCODE -ne 0) {
    Step "PySide6 校验失败。"
    exit 1
}

Step "运行时准备完成。"
exit 0
