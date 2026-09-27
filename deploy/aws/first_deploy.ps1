# First deploy of the SecureRAG demo backend (Windows PowerShell 5.1+). Run it yourself:
#
#   powershell -ExecutionPolicy Bypass -File deploy\aws\first_deploy.ps1
#
# Prerequisite: deploy\aws\setup_credentials.ps1 (AWS profile "securerag", SSM secrets, gh login).
# Steps (each is skipped when already done, so re-running is safe):
#   1. push main to GitHub
#   2. $20/month budget with email alerts at $2/$5/$10 actual and $20 forecast       ($0)
#   3. GitHub OIDC identity provider in IAM                                          ($0)
#   4. stack pass 1: ECR repo, log group, IAM roles                                  ($0)
#   5. GitHub repo variables for the deploy workflow
#   6. deploy workflow on GitHub Actions builds + pushes the image (bootstrap mode)  (~$0.02/month ECR)
#   7. stack pass 2: Lambda function + Function URL                                  (free tier)
#   8. FUNCTION_URL variable + smoke test of every demo role
# Never enables WAF, provisioned concurrency, NAT or EC2.

$ErrorActionPreference = 'Continue'
$Repo       = 'Ayush1860/SecureRag'
$AwsProfile = 'securerag'
$Region     = 'ap-south-1'
$Stack      = 'securerag'
$Root       = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
Set-Location $Root
$Py = Join-Path $Root '.venv\Scripts\python.exe'
if (-not (Test-Path $Py)) { $Py = 'python' }
$env:AWS_PROFILE = $AwsProfile
$env:AWS_REGION = $Region
$Utf8 = New-Object System.Text.UTF8Encoding($false)

function Step($msg) { Write-Host "`n== $msg" -ForegroundColor Cyan }
function Fail($msg) { Write-Host "ERROR: $msg" -ForegroundColor Red; exit 1 }
function JsonFile($obj) {
    $tmp = [IO.Path]::GetTempFileName()
    [IO.File]::WriteAllText($tmp, ($obj | ConvertTo-Json -Depth 10), $Utf8)
    return $tmp
}
function StackOutput($key) {
    aws cloudformation describe-stacks --stack-name $Stack `
        --query "Stacks[0].Outputs[?OutputKey=='$key'].OutputValue | [0]" --output text
}

$null = aws sts get-caller-identity 2>$null
if ($LASTEXITCODE -ne 0) { Fail "AWS profile '$AwsProfile' has no working credentials; run setup_credentials.ps1" }
$Account = aws sts get-caller-identity --query Account --output text
Write-Host "AWS account $Account, region $Region"

# --- 1. push ---------------------------------------------------------------------------------------
Step 'Push main to GitHub'
if (git status --porcelain) { Fail 'working tree has uncommitted changes; commit or stash them first' }
git push origin main
if ($LASTEXITCODE -ne 0) { Fail 'git push failed' }
$Sha = (git rev-parse HEAD).Trim()

# --- 2. budget -------------------------------------------------------------------------------------
Step 'Budget alert ($20/month)'
$existing = aws budgets describe-budgets --account-id $Account --query "Budgets[?BudgetName=='securerag-monthly'].BudgetName" --output text 2>$null
if ($existing -eq 'securerag-monthly') {
    Write-Host '  already exists'
} else {
    $default = (git config user.email)
    $email = Read-Host "  Email for budget alerts [$default]"
    if (-not $email) { $email = $default }
    $budget = JsonFile @{ BudgetName = 'securerag-monthly'; BudgetType = 'COST'; TimeUnit = 'MONTHLY';
                          BudgetLimit = @{ Amount = '20'; Unit = 'USD' } }
    $notes = @()
    foreach ($n in @(@('ACTUAL', 10), @('ACTUAL', 25), @('ACTUAL', 50), @('FORECASTED', 100))) {
        $notes += @{ Notification = @{ NotificationType = $n[0]; ComparisonOperator = 'GREATER_THAN';
                                       Threshold = $n[1]; ThresholdType = 'PERCENTAGE' };
                     Subscribers = @(@{ SubscriptionType = 'EMAIL'; Address = $email }) }
    }
    $notesFile = JsonFile $notes
    aws budgets create-budget --account-id $Account --budget "file://$($budget -replace '\\','/')" `
        --notifications-with-subscribers "file://$($notesFile -replace '\\','/')"
    $ok = $LASTEXITCODE
    Remove-Item $budget, $notesFile -Force
    if ($ok -ne 0) { Fail 'create-budget failed' }
    Write-Host "  created; alerts at `$2/`$5/`$10 actual and `$20 forecast -> $email"
}

# --- 3. OIDC provider ------------------------------------------------------------------------------
Step 'GitHub OIDC identity provider'
$oidcArn = "arn:aws:iam::${Account}:oidc-provider/token.actions.githubusercontent.com"
$null = aws iam get-open-id-connect-provider --open-id-connect-provider-arn $oidcArn 2>$null
if ($LASTEXITCODE -eq 0) {
    Write-Host '  already exists'
} else {
    aws iam create-open-id-connect-provider --url https://token.actions.githubusercontent.com `
        --client-id-list sts.amazonaws.com --tags Key=project,Value=securerag | Out-Null
    if ($LASTEXITCODE -ne 0) { Fail 'could not create the OIDC provider' }
    Write-Host '  created'
}

# --- 4. stack pass 1 -------------------------------------------------------------------------------
Step 'Stack pass 1 (ECR, log group, roles)'
aws cloudformation deploy --stack-name $Stack --template-file deploy/aws/backend.yaml `
    --capabilities CAPABILITY_IAM --tags project=securerag --no-fail-on-empty-changeset
if ($LASTEXITCODE -ne 0) { Fail 'stack pass 1 failed (see the Events tab of the stack in the console)' }
$RoleArn = StackOutput 'DeployRoleArn'
$EcrUri  = StackOutput 'EcrRepositoryUri'
$FnName  = StackOutput 'FunctionName'
Write-Host "  role $RoleArn`n  ecr  $EcrUri`n  fn   $FnName"

# --- 5. GitHub variables ---------------------------------------------------------------------------
Step 'GitHub repo variables'
gh variable set AWS_DEPLOY_ROLE_ARN --repo $Repo --body $RoleArn
gh variable set ECR_REPOSITORY_URI  --repo $Repo --body $EcrUri
gh variable set LAMBDA_FUNCTION_NAME --repo $Repo --body $FnName
if ($LASTEXITCODE -ne 0) { Fail 'gh variable set failed' }

# --- 6. image build on GitHub Actions --------------------------------------------------------------
Step "Image $EcrUri`:$Sha"
$null = aws ecr describe-images --repository-name ($EcrUri.Split('/')[-1]) --image-ids imageTag=$Sha 2>$null
if ($LASTEXITCODE -eq 0) {
    Write-Host '  already in ECR'
} else {
    $before = gh run list --repo $Repo --workflow deploy-backend.yml --limit 1 --json databaseId -q '.[0].databaseId'
    gh workflow run deploy-backend.yml --repo $Repo --ref main
    if ($LASTEXITCODE -ne 0) { Fail 'could not start the deploy workflow' }
    Write-Host '  started the workflow; waiting for it to appear...'
    $runId = $before
    for ($i = 0; $i -lt 30 -and $runId -eq $before; $i++) {
        Start-Sleep -Seconds 4
        $runId = gh run list --repo $Repo --workflow deploy-backend.yml --limit 1 --json databaseId -q '.[0].databaseId'
    }
    if ($runId -eq $before) { Fail 'the workflow run did not appear; check the Actions tab' }
    Write-Host "  https://github.com/$Repo/actions/runs/$runId (build takes ~15-25 min)"
    gh run watch $runId --repo $Repo --exit-status --interval 30
    if ($LASTEXITCODE -ne 0) { Fail "workflow run $runId failed; open the link above" }
}
$Image = "$EcrUri`:$Sha"

# --- 7. stack pass 2 -------------------------------------------------------------------------------
Step 'Stack pass 2 (Lambda function + Function URL)'
aws cloudformation deploy --stack-name $Stack --template-file deploy/aws/backend.yaml `
    --capabilities CAPABILITY_IAM --tags project=securerag --no-fail-on-empty-changeset `
    --parameter-overrides "ImageUri=$Image"
if ($LASTEXITCODE -ne 0) { Fail 'stack pass 2 failed (see the Events tab of the stack in the console)' }
$FnUrl = (StackOutput 'FunctionUrl').TrimEnd('/')
Write-Host "  Function URL: $FnUrl"

# --- 8. FUNCTION_URL + smoke test ------------------------------------------------------------------
Step 'Smoke test every demo role (first call is a ~20 s cold start)'
gh variable set FUNCTION_URL --repo $Repo --body $FnUrl
& $Py scripts/smoke_api.py --base-url $FnUrl --keys-file data/demo_keys_plain.json --wait-ready 300
if ($LASTEXITCODE -ne 0) { Fail "smoke test failed; logs: aws logs tail /aws/lambda/$FnName --since 15m" }

Step 'Backend is live'
Write-Host "  Function URL : $FnUrl"
Write-Host "  Image        : $Image"
Write-Host '  Next: Amplify frontend, deploy\aws\README.md section 2 (VITE_API_BASE = the Function URL).'
Write-Host '  Tell Claude "backend done" plus the Amplify domain once it exists.' -ForegroundColor Green
