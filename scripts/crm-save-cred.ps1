param([Parameter(Mandatory=$true)][string]$OutFile)
# Reads "username" then "password" from stdin (never from the command line) and stores them DPAPI-encrypted:
# only this Windows user on this machine can decrypt the file. stdin is read as UTF-8 through a stream (the app runs this
# without a console window, where the [Console] encoding setters are not available).
$ErrorActionPreference = 'Stop'
$reader = New-Object System.IO.StreamReader([Console]::OpenStandardInput(), [System.Text.Encoding]::UTF8)
$user = $reader.ReadLine()
$pass = $reader.ReadLine()
$cred = New-Object System.Management.Automation.PSCredential($user, (ConvertTo-SecureString $pass -AsPlainText -Force))
$cred | Export-Clixml -Path $OutFile
