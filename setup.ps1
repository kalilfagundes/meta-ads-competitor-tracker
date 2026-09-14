<#
Creates .env for docker compose by asking a few questions (Windows).

  powershell -ExecutionPolicy Bypass -File .\setup.ps1            interactive
  powershell -ExecutionPolicy Bypass -File .\setup.ps1 -Defaults  no questions: bundled Postgres and Garage, port 8000
  powershell -ExecutionPolicy Bypass -File .\setup.ps1 -Force     allow replacing an existing .env

The bundled services' users and passwords can be typed in or left to the
defaults (passwords are then generated); the session secret is always generated.
Everything else (admin account, competitors, country, schedule) is set in the
browser afterwards. See .env.example for every variable.
#>
param(
    [switch]$Defaults,
    [switch]$Force
)

$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot
$envPath = Join-Path $PSScriptRoot ".env"

function New-Secret {
    $chars = [char[]]"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"
    $bytes = New-Object byte[] 40
    $rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    $result = ""
    while ($result.Length -lt 40) {
        $rng.GetBytes($bytes)
        foreach ($b in $bytes) {
            # 248 = 62 * 4: skip the remainder so every character is equally likely
            if ($b -lt 248 -and $result.Length -lt 40) { $result += $chars[$b % 62] }
        }
    }
    $rng.Dispose()
    return $result
}

function New-HexSecret {
    $bytes = New-Object byte[] 32
    $rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    $rng.GetBytes($bytes)
    $rng.Dispose()
    return -join ($bytes | ForEach-Object { $_.ToString("x2") })
}

function Ask([string]$question, [string]$default) {
    if ($Defaults) { return $default }
    $answer = Read-Host "$question [$default]"
    if ([string]::IsNullOrWhiteSpace($answer)) { return $default }
    return $answer
}

function Ask-Required([string]$question) {
    while ($true) {
        $answer = Read-Host $question
        if (-not [string]::IsNullOrWhiteSpace($answer)) { return $answer }
        Write-Host "  This value is required."
    }
}

function Ask-Secret([string]$question) {
    while ($true) {
        $secure = Read-Host $question -AsSecureString
        $ptr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
        try { $answer = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($ptr) }
        finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($ptr) }
        if (-not [string]::IsNullOrWhiteSpace($answer)) { return $answer }
        Write-Host "  This value is required."
    }
}

# Lowercase letters, digits and _, starting with a letter; Postgres reserves pg_.
function Ask-Username([string]$question, [string]$default, [int]$minLength) {
    while ($true) {
        $answer = Ask $question $default
        if ($answer -cnotmatch '^[a-z][a-z0-9_]*$') { Write-Host "  Use lowercase letters, digits and _, starting with a letter." }
        elseif ($answer.StartsWith("pg_")) { Write-Host "  Names starting with pg_ are reserved." }
        elseif ($answer.Length -lt $minLength) { Write-Host "  Use at least $minLength characters." }
        else { return $answer }
    }
}

# Not echoed; empty = generate one.
# At least 16 characters, no spaces or quotes (Garage's rule for secret keys).
function Ask-Password([string]$question) {
    if ($Defaults) { return New-Secret }
    while ($true) {
        $secure = Read-Host "$question (Enter to generate one)" -AsSecureString
        $ptr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
        try { $answer = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($ptr) }
        finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($ptr) }
        if ([string]::IsNullOrEmpty($answer)) { return New-Secret }
        if ($answer -cnotmatch "^[!-&(-~]+$") { Write-Host "  Use letters, digits and symbols, without spaces or '." }
        elseif ($answer.Length -lt 16) { Write-Host "  Use at least 16 characters." }
        else { return $answer }
    }
}

# A value typed by the user, quoted for .env (single quotes: taken literally).
function Quoted([string]$value) {
    if ($value.Contains("'")) { throw "Values can't contain a single quote (')." }
    return "'$value'"
}

if ((Test-Path $envPath) -and -not $Force) {
    if ($Defaults) {
        Write-Host ".env already exists; leaving it alone (use -Force to replace it)."
        exit 0
    }
    Write-Host ".env already exists."
    Write-Host "Existing data keeps the old users and passwords, so the new ones won't be accepted."
    $answer = Read-Host "Replace it? [y/N]"
    if ($answer -notmatch '^(y|yes)$') { Write-Host "Nothing changed."; exit 0 }
}

Write-Host "This creates .env for docker compose. Press Enter to accept the value in brackets."
Write-Host ""

$webPort = Ask "Port for the web app" "8000"

Write-Host ""
Write-Host "Media storage (a copy of every ad image and video):"
Write-Host "  1) Built-in Garage, runs with the app (recommended)"
Write-Host "  2) External S3-compatible bucket (AWS S3, Cloudflare R2, Backblaze B2...)"
$storage = Ask "Choose" "1"

if ($storage -eq "2") {
    $endpoint = if ($Defaults) { "" } else { Read-Host "Endpoint URL (leave empty for AWS S3)" }
    $bucket = Ask-Required "Bucket"
    $region = Ask "Region" "auto"
    $accessKey = Ask-Required "Access key ID"
    $secretKey = Ask-Secret "Secret access key"
    $pathStyle = if ((Ask "Path-style addressing? (some self-hosted providers need it) y/N" "n") -match '^(y|yes)$') { "true" } else { "false" }
    $storageProfile = ""
    $s3Lines = @(
        "S3_ENDPOINT=$(Quoted $endpoint)",
        "S3_BUCKET=$(Quoted $bucket)",
        "S3_REGION=$(Quoted $region)",
        "S3_ACCESS_KEY_ID=$(Quoted $accessKey)",
        "S3_SECRET_ACCESS_KEY=$(Quoted $secretKey)",
        "S3_FORCE_PATH_STYLE=$pathStyle"
    )
} else {
    $storageKey = Ask-Username "Storage access key (like a user name)" "tracker_media" 8
    $storageSecret = Ask-Password "Storage secret key"
    $storageProfile = "garage"
    $s3Lines = @(
        "S3_ENDPOINT=http://garage:3900",
        "S3_BUCKET=ads-media",
        "S3_REGION=garage",
        "S3_ACCESS_KEY_ID=$(Quoted $storageKey)",
        "S3_SECRET_ACCESS_KEY=$(Quoted $storageSecret)",
        "S3_FORCE_PATH_STYLE=true",
        "GARAGE_RPC_SECRET=$(New-HexSecret)",
        "GARAGE_ADMIN_TOKEN=$(New-Secret)"
    )
}

Write-Host ""
Write-Host "Database:"
Write-Host "  1) Built-in Postgres, runs with the app (recommended)"
Write-Host "  2) Your own Postgres (14 or newer)"
$database = Ask "Choose" "1"

if ($database -eq "2") {
    $dbHost = Ask-Required "Host"
    $dbPort = Ask "Port" "5432"
    $dbName = Ask "Database name" "tracker"
    $dbUser = Ask-Required "User"
    $dbPassword = Ask-Secret "Password"
    $sslMode = Ask "SSL mode (disable, require, verify-full)" "require"
    $dbProfile = ""
    $dbLines = @(
        "POSTGRES_HOST=$(Quoted $dbHost)",
        "POSTGRES_PORT=$(Quoted $dbPort)",
        "POSTGRES_DB=$(Quoted $dbName)",
        "POSTGRES_USER=$(Quoted $dbUser)",
        "POSTGRES_PASSWORD=$(Quoted $dbPassword)",
        "POSTGRES_SSLMODE=$(Quoted $sslMode)"
    )
} else {
    $dbUser = Ask-Username "Database user" "tracker" 3
    $dbPassword = Ask-Password "Database password"
    $dbProfile = "postgres"
    $dbLines = @(
        "POSTGRES_HOST=postgres",
        "POSTGRES_PORT=5432",
        "POSTGRES_DB=tracker",
        "POSTGRES_USER=$(Quoted $dbUser)",
        "POSTGRES_PASSWORD=$(Quoted $dbPassword)",
        "POSTGRES_SSLMODE="
    )
}

$profiles = (@($dbProfile, $storageProfile) | Where-Object { $_ }) -join ","
$setupToken = (New-Secret).Substring(0, 24)

$lines = @(
    "# Created by setup.ps1. Keep this file private: it holds passwords.",
    "# Every variable is described in .env.example.",
    "",
    "# Bundled services to run: postgres, garage (empty = use your own).",
    "COMPOSE_PROFILES=$profiles",
    "WEB_PORT=$(Quoted $webPort)",
    "",
    "# Database"
) + $dbLines + @(
    "",
    "# Media storage"
) + $s3Lines + @(
    "",
    "# Signs the login cookie.",
    "SESSION_SECRET=$(New-Secret)",
    "",
    "# Asked by the setup wizard when creating the administrator.",
    "SETUP_TOKEN=$setupToken",
    ""
)

# UTF-8 without BOM and LF line endings, as docker compose expects.
[System.IO.File]::WriteAllText($envPath, ($lines -join "`n"), (New-Object System.Text.UTF8Encoding $false))

# Only the current user can read it: drop the inherited permissions and grant this
# user alone. icacls needs no admin rights (Set-Acl would, to rewrite the audit list).
$identity = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
& icacls $envPath /inheritance:r /grant:r "${identity}:F" | Out-Null
if ($LASTEXITCODE -ne 0) {
    Write-Warning "Couldn't restrict .env to your user; it holds passwords, so keep it private."
}

Write-Host ""
Write-Host "Created .env."
Write-Host "Next: docker compose up -d"
Write-Host "Then open http://localhost:$webPort and follow the setup wizard."
Write-Host "Setup code (asked when you create the administrator): $setupToken"
