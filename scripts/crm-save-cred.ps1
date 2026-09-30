param([Parameter(Mandatory=$true)][string]$OutFile)
# Reads "username" then "password" from stdin (never from the command line) and stores them DPAPI-encrypted:
# only this Windows user on this machine can decrypt the file.
$ErrorActionPreference = 'Stop'
$user = [Console]::In.ReadLine()
$pass = [Console]::In.ReadLine()
$cred = New-Object System.Management.Automation.PSCredential($user, (ConvertTo-SecureString $pass -AsPlainText -Force))
$cred | Export-Clixml -Path $OutFile
