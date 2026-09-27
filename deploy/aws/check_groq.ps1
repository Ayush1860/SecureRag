# Checks the Groq key stored in SSM against the Groq API without printing the key.
#
#   powershell -ExecutionPolicy Bypass -File deploy\aws\check_groq.ps1 [-Model openai/gpt-oss-20b]
#
# Prints the chat model ids the key can use, then tries one short completion with -Model and shows
# Groq's own error message if it fails (e.g. a retired model or one blocked for your org).
param([string]$Model = 'openai/gpt-oss-20b')

$key = (aws ssm get-parameter --profile securerag --region ap-south-1 --name /securerag/groq_api_key `
        --with-decryption --query Parameter.Value --output text)
if (-not $key) { Write-Host 'could not read /securerag/groq_api_key' -ForegroundColor Red; exit 1 }
$headers = @{ Authorization = "Bearer $($key.Trim())" }
Remove-Variable key

Write-Host '== models this key can use'
try {
    (Invoke-RestMethod -Uri https://api.groq.com/openai/v1/models -Headers $headers).data |
        Where-Object { $_.active -ne $false } | Sort-Object id | ForEach-Object { "  $($_.id)" }
} catch { Write-Host "  list failed: $($_.ErrorDetails.Message)" -ForegroundColor Red }

Write-Host "`n== test completion with $Model"
$body = @{ model = $Model; max_tokens = 200; messages = @(@{ role = 'user'; content = 'Say OK' }) } | ConvertTo-Json -Depth 5
try {
    $r = Invoke-RestMethod -Method Post -Uri https://api.groq.com/openai/v1/chat/completions `
        -Headers $headers -ContentType 'application/json' -Body $body
    Write-Host "  OK: $($r.choices[0].message.content)" -ForegroundColor Green
} catch { Write-Host "  failed: $($_.ErrorDetails.Message)" -ForegroundColor Red }
