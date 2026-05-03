param(
    [switch]$Install
)

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path

if ($Install) {
    python -m pip install -r (Join-Path $ScriptDir "requirements.txt")
}

python (Join-Path $ScriptDir "recording_app.py")
