param([Parameter(Mandatory=$true)][string]$Path, [Parameter(Mandatory=$true)][string]$CredFile)
# READ-ONLY CRM Web API GET as the person who saved the credential. Prints the JSON response.
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$cred = Import-Clixml -Path $CredFile
$base = 'http://crm.snapppay.ir/CRM-SnappPay-DB/api/data/v9.0/'
$uri = if ($Path -match '^https?://') { $Path } else { $base + $Path }
$headers = @{ 'Accept' = 'application/json'; 'OData-MaxVersion' = '4.0'; 'OData-Version' = '4.0'; 'Prefer' = 'odata.maxpagesize=5000' }
try {
  $resp = Invoke-RestMethod -Uri $uri -Method GET -Credential $cred -Headers $headers -TimeoutSec 180
  ($resp | ConvertTo-Json -Depth 12 -Compress) -replace '[\x00-\x1F]', ''
} catch {
  [Console]::Error.WriteLine($_.Exception.Message)
  exit 1
}
