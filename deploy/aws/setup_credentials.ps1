# One-time credential setup for the AWS demo deploy (Windows PowerShell 5.1+).
#
#   powershell -ExecutionPolicy Bypass -File deploy\aws\setup_credentials.ps1
#
# Run it yourself in a terminal: it prompts for every secret and sends it straight to AWS SSM or
# GitHub, so no secret is printed, committed, or passed to anyone else. It creates only:
#   * a GitHub CLI login and an AWS CLI profile "securerag" (local config)
#   * SSM SecureString parameters /securerag/{aes_key_b64,api_keys_json,groq_api_key} ($0)
#   * the GitHub Actions secret DEMO_KEYS_JSON
# Re-running is safe: existing parameters/secrets are kept unless you answer "y" to overwrite.

$ErrorActionPreference = 'Continue'
$Repo    = 'Ayush1860/SecureRag'
$AwsProfile = 'securerag'
$Region  = 'ap-south-1'
$Root    = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
Set-Location $Root
$Py = Join-Path $Root '.venv\Scripts\python.exe'
if (-not (Test-Path $Py)) { $Py = 'python' }
$Utf8 = New-Object System.Text.UTF8Encoding($false)

function Step($msg) { Write-Host "`n== $msg" -ForegroundColor Cyan }
function Fail($msg) { Write-Host "ERROR: $msg" -ForegroundColor Red; exit 1 }
function AskYes($msg) { (Read-Host "$msg [y/N]") -match '^[yY]' }

function Put-SecureParam($name, $value) {
    # Via a temp file (not the command line), deleted right after.
    $tmp = [IO.Path]::GetTempFileName()
    try {
        [IO.File]::WriteAllText($tmp, $value, $Utf8)
        $uri = 'file://' + ($tmp -replace '\\', '/')
        $null = aws ssm get-parameter --name $name --profile $AwsProfile --region $Region 2>$null
        if ($LASTEXITCODE -eq 0) {
            $null = aws ssm put-parameter --name $name --type SecureString --value $uri --overwrite `
                --profile $AwsProfile --region $Region
        } else {
            $null = aws ssm put-parameter --name $name --type SecureString --value $uri `
                --tags Key=project,Value=securerag --profile $AwsProfile --region $Region
        }
        if ($LASTEXITCODE -ne 0) { Fail "could not write $name" }
        Write-Host "  stored $name"
    } finally { Remove-Item $tmp -Force -ErrorAction SilentlyContinue }
}

function Param-Exists($name) {
    $null = aws ssm get-parameter --name $name --profile $AwsProfile --region $Region 2>$null
    return ($LASTEXITCODE -eq 0)
}

# --- 1. GitHub CLI -------------------------------------------------------------------------------
Step 'GitHub CLI login (needs the "workflow" scope to push workflow files and set secrets)'
$null = gh auth status 2>$null
if ($LASTEXITCODE -ne 0) {
    gh auth login --hostname github.com --git-protocol https --web --scopes workflow
    if ($LASTEXITCODE -ne 0) { Fail 'gh auth login failed' }
} else {
    Write-Host '  already logged in; adding the workflow scope if missing'
    gh auth refresh --hostname github.com --scopes workflow
}
gh auth setup-git | Out-Null

# --- 2. AWS CLI profile ----------------------------------------------------------------------------
Step "AWS CLI profile '$AwsProfile' (region $Region)"
$null = aws sts get-caller-identity --profile $AwsProfile 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host '  1) aws login     - sign in through the browser with your console user (recommended)'
    Write-Host '  2) access keys   - an IAM user access key id + secret (aws configure)'
    Write-Host '  Tip: use an IAM user/Identity Center user with AdministratorAccess, not the root user.'
    $choice = Read-Host '  Choose 1 or 2'
    if ($choice -eq '2') {
        aws configure --profile $AwsProfile
    } else {
        aws login --profile $AwsProfile --region $Region
    }
    aws configure set region $Region --profile $AwsProfile
    $null = aws sts get-caller-identity --profile $AwsProfile 2>$null
    if ($LASTEXITCODE -ne 0) { Fail "AWS profile '$AwsProfile' still has no working credentials" }
}
$arn = aws sts get-caller-identity --profile $AwsProfile --query Arn --output text
Write-Host "  signed in as $arn"
if ($arn -match ':root$') { Write-Host '  WARNING: this is the root user. It works, but an IAM user is safer.' -ForegroundColor Yellow }

# --- 3. SSM parameters -----------------------------------------------------------------------------
Step 'AES-256 key for the demo index -> /securerag/aes_key_b64 (freshly generated, never your local key)'
if ((Param-Exists '/securerag/aes_key_b64') -and -not (AskYes '  already exists; generate a NEW one? (the next image build re-encrypts with it)')) {
    Write-Host '  kept existing'
} else {
    $aes = (& $Py scripts/generate_key.py | Select-Object -Last 1).Trim()
    if ($aes.Length -lt 40) { Fail 'key generation failed' }
    Put-SecureParam '/securerag/aes_key_b64' $aes
    Remove-Variable aes
}

Step 'Demo API keys (guest/employee/exec) -> SSM /securerag/api_keys_json + GitHub secret DEMO_KEYS_JSON'
$plainFile  = Join-Path $Root 'data\demo_keys_plain.json'
$hashedFile = Join-Path $Root 'data\demo_api_keys.hashed.json'
$makeKeys = -not ((Param-Exists '/securerag/api_keys_json') -and (Test-Path $plainFile))
if (-not $makeKeys) { $makeKeys = AskYes '  demo keys already exist; rotate them?' }
if ($makeKeys) {
    $plain = (& $Py scripts/create_demo_keys.py --hashed-out $hashedFile 2>$null | Select-Object -Last 1)
    if ($LASTEXITCODE -ne 0 -or -not $plain) { Fail 'create_demo_keys.py failed' }
    [IO.File]::WriteAllText($plainFile, $plain, $Utf8)          # data/ is git-ignored
    Put-SecureParam '/securerag/api_keys_json' ([IO.File]::ReadAllText($hashedFile))
    Remove-Variable plain
} else { Write-Host '  kept existing' }
Get-Content -Raw $plainFile | gh secret set DEMO_KEYS_JSON --repo $Repo
if ($LASTEXITCODE -ne 0) { Fail 'could not set GitHub secret DEMO_KEYS_JSON' }
Write-Host "  GitHub secret DEMO_KEYS_JSON set (plaintext copy stays in data\demo_keys_plain.json, git-ignored)"

Step 'Groq API key -> /securerag/groq_api_key (optional: without it the demo refuses to answer questions)'
Write-Host '  Free key: https://console.groq.com/keys . Press Enter to skip.'
if ((Param-Exists '/securerag/groq_api_key') -and -not (AskYes '  already stored; replace it?')) {
    Write-Host '  kept existing'
} else {
    $sec = Read-Host '  Groq API key (input hidden)' -AsSecureString
    $bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($sec)
    try { $groq = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr) }
    finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr) }
    if ($groq.Trim()) { Put-SecureParam '/securerag/groq_api_key' $groq.Trim() } else { Write-Host '  skipped' }
    Remove-Variable groq
}

Step 'Done'
Write-Host "  AWS profile : $AwsProfile ($arn)"
Write-Host '  SSM         : /securerag/aes_key_b64, /securerag/api_keys_json (+ groq_api_key if given)'
Write-Host "  GitHub      : logged in; secret DEMO_KEYS_JSON set on $Repo"
Write-Host '  Tell Claude "credentials done" to continue with the deploy.' -ForegroundColor Green
