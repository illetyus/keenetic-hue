param([Parameter(Mandatory=$true)][string]$Path)
$ErrorActionPreference = 'Stop'
try {
    # Python may inherit PowerShell 7's PSModulePath while invoking Windows PowerShell.
    # Load the OS security cmdlets from this host's own directory, without changing global settings.
    $securityModule = [IO.Path]::Combine($PSHOME, 'Modules\Microsoft.PowerShell.Security\Microsoft.PowerShell.Security.psd1')
    Import-Module -Name $securityModule -ErrorAction Stop
    $currentSid = [System.Security.Principal.WindowsIdentity]::GetCurrent().User.Value
    $allowed = @($currentSid, 'S-1-5-18', 'S-1-5-32-544')
    $acl = Get-Acl -LiteralPath $Path
    $ownerSid = $acl.GetOwner([System.Security.Principal.SecurityIdentifier]).Value
    if ($ownerSid -notin $allowed) { exit 2 }
    foreach ($entry in $acl.Access) {
        if ($entry.AccessControlType -eq [System.Security.AccessControl.AccessControlType]::Allow) {
            $sid = $entry.IdentityReference.Translate([System.Security.Principal.SecurityIdentifier]).Value
            if ($sid -notin $allowed) { exit 2 }
        }
    }
    exit 0
} catch { exit 2 }
