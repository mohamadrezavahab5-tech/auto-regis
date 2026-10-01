param([Parameter(Mandatory=$true)][string]$Path, [Parameter(Mandatory=$true)][string]$CredFile)
# READ-ONLY CRM Web API GET as the person who saved the credential. Writes CRM's own JSON bytes to stdout unchanged
# (no re-serialising: exact UTF-8 text, and much faster on 5,000-row pages).
$ErrorActionPreference = 'Stop'
$cred = Import-Clixml -Path $CredFile
$base = 'http://crm.snapppay.ir/CRM-SnappPay-DB/api/data/v9.0/'
$uri = if ($Path -match '^https?://') { $Path } else { $base + $Path }
$headers = @{ 'Accept' = 'application/json'; 'OData-MaxVersion' = '4.0'; 'OData-Version' = '4.0'; 'Prefer' = 'odata.include-annotations="*",odata.maxpagesize=5000' }
try {
  $r = Invoke-WebRequest -Uri $uri -Method GET -Credential $cred -Headers $headers -TimeoutSec 180 -UseBasicParsing
  $bytes = $r.RawContentStream.ToArray()
  $out = [Console]::OpenStandardOutput()
  $out.Write($bytes, 0, $bytes.Length)
  $out.Flush()
} catch {
  $code = $null
  if ($_.Exception.Response) { $code = [int]$_.Exception.Response.StatusCode }
  if ($code) { [Console]::Error.WriteLine("HTTP $code " + $_.Exception.Message) } else { [Console]::Error.WriteLine($_.Exception.Message) }
  exit 1
}
