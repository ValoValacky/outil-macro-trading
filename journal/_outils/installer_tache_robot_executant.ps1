# Installe la tache Windows "Robot Executant (observation)" : un passage toutes les 15 minutes,
# du lundi au vendredi. PHASE 1 : le robot n'envoie AUCUN ordre, il ecrit seulement ce qu'il ferait dans
# journal\_executant\derniere_decision.md.
# A lancer dans PowerShell OUVERT EN ADMINISTRATEUR :
#   powershell -ExecutionPolicy Bypass -File "<chemin>\installer_tache_robot_executant.ps1"

$Dossier = Split-Path -Parent $MyInvocation.MyCommand.Path
$Python  = (Get-Command python).Source
$Script  = Join-Path $Dossier "robot_executant.py"
$Nom     = "Robot Executant (observation)"

$Action  = New-ScheduledTaskAction -Execute $Python -Argument "-u `"$Script`"" -WorkingDirectory $Dossier
$Debut   = (Get-Date).Date.AddMinutes(2)
$Trigger = New-ScheduledTaskTrigger -Once -At $Debut -RepetitionInterval (New-TimeSpan -Minutes 15) -RepetitionDuration (New-TimeSpan -Days 3650)
$Reglages = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 10) -StartWhenAvailable

Register-ScheduledTask -TaskName $Nom -Action $Action -Trigger $Trigger -Settings $Reglages -Force | Out-Null
$env:PYTHONIOENCODING = "utf-8"
Get-ScheduledTask -TaskName $Nom | Select-Object TaskName, State
Write-Host "Tache installee. Le robot ecrit dans journal\_executant\ (aucun ordre envoye)."
