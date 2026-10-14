<# Create the release signing key for MICO360 Meetings for Android (run once, by the owner).

     powershell -File android\tools\make-keystore.ps1 [-Out "$env:USERPROFILE\mico360-keys\mico360-meetings.jks"]

   keytool asks for the passwords itself; this script never sees or prints them.
   Keep the .jks file and its passwords in a safe backup: every future update must be
   signed with the same key, or phones will refuse to install it over the old version. #>
param([string]$Out = "$env:USERPROFILE\mico360-keys\mico360-meetings.jks",
      [string]$Alias = "mico360-meetings")
$ErrorActionPreference = "Stop"

$keytool = if ($env:JAVA_HOME) { Join-Path $env:JAVA_HOME "bin\keytool.exe" } else { "keytool" }
if ((Test-Path $Out)) { throw "$Out already exists. Never replace a release key that has been used." }
New-Item -ItemType Directory -Force (Split-Path $Out) | Out-Null

& $keytool -genkeypair -v -keystore $Out -alias $Alias -keyalg RSA -keysize 4096 -validity 10000 `
  -dname "CN=MICO360 Meetings, O=MICO360, C=OM"
if ($LASTEXITCODE) { throw "keytool failed" }

$b64 = "$Out.base64.txt"
[IO.File]::WriteAllText($b64, [Convert]::ToBase64String([IO.File]::ReadAllBytes($Out)))

Write-Output ""
Write-Output "Created $Out"
Write-Output "Now store it as GitHub secrets (gh asks for the password values, which you type)."
Write-Output "This keystore is PKCS12, which has ONE password: give ANDROID_KEY_PASSWORD the same value as ANDROID_KEYSTORE_PASSWORD."
Write-Output "  Get-Content `"$b64`" | gh secret set ANDROID_KEYSTORE_BASE64"
Write-Output "  gh secret set ANDROID_KEYSTORE_PASSWORD"
Write-Output "  gh secret set ANDROID_KEY_PASSWORD"
Write-Output "  gh secret set ANDROID_KEY_ALIAS --body $Alias"
Write-Output "Then delete $b64, and back up $Out with its passwords somewhere safe."
