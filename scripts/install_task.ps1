#Requires -Version 5.1
<#
.SYNOPSIS
    Registra o Windows Maintenance Agent no Agendador de Tarefas do Windows.

.DESCRIPTION
    Cria a tarefa agendada que executa "maintenance run" periodicamente.

    - O script NÃO se auto-eleva e NÃO contorna o UAC: execute-o em um PowerShell
      aberto com "Executar como administrador".
    - A tarefa fica visível no Agendador de Tarefas e gera logs normalmente na pasta
      logs\ do agente.
    - Use -WhatIf para ver o que seria registrado sem alterar nada.

.PARAMETER TaskName
    Nome da tarefa (padrão: "Windows Maintenance Agent").

.PARAMETER Schedule
    Daily ou Weekly (padrão: Weekly).

.PARAMETER DayOfWeek
    Dia da semana quando Schedule = Weekly (padrão: Monday).

.PARAMETER At
    Horário no formato HH:mm (padrão: 08:00).

.PARAMETER RunAs
    System (padrão): conta SYSTEM, necessária para instalar atualizações sem usuário
    logado. CurrentUser: usuário atual, apenas enquanto estiver logado (nenhuma senha
    é armazenada).

.PARAMETER AgentPath
    Caminho do maintenance.exe. Se omitido, usa dist\maintenance.exe do projeto ou,
    na ausência dele, o ambiente virtual .venv do projeto.

.PARAMETER ConfigPath
    Arquivo de configuração a usar (opcional; padrão: config.json na pasta do agente).

.PARAMETER ExecutionTimeLimitHours
    Tempo máximo de execução antes de o Agendador encerrar a tarefa (padrão: 4).

.PARAMETER Force
    Substitui uma tarefa existente com o mesmo nome.

.EXAMPLE
    .\scripts\install_task.ps1 -WhatIf

.EXAMPLE
    .\scripts\install_task.ps1 -Schedule Weekly -DayOfWeek Wednesday -At 12:30

.EXAMPLE
    .\scripts\install_task.ps1 -AgentPath 'C:\TI\WindowsMaintenanceAgent\maintenance.exe' -Force
#>
[CmdletBinding(SupportsShouldProcess = $true)]
param(
    [ValidateNotNullOrEmpty()]
    [string]$TaskName = 'Windows Maintenance Agent',

    [ValidateSet('Daily', 'Weekly')]
    [string]$Schedule = 'Weekly',

    [ValidateSet('Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday')]
    [string]$DayOfWeek = 'Monday',

    [ValidatePattern('^([01]\d|2[0-3]):[0-5]\d$')]
    [string]$At = '08:00',

    [ValidateSet('System', 'CurrentUser')]
    [string]$RunAs = 'System',

    [string]$AgentPath,

    [string]$ConfigPath,

    [ValidateRange(1, 24)]
    [int]$ExecutionTimeLimitHours = 4,

    [switch]$Force
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Test-IsElevated {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($identity)
    return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

$projectRoot = Split-Path -Parent $PSScriptRoot

# --- Comando a executar -------------------------------------------------------------
if ($AgentPath) {
    $execute = (Resolve-Path -LiteralPath $AgentPath).ProviderPath
    if ([IO.Path]::GetExtension($execute) -ne '.exe') {
        throw "AgentPath deve apontar para o maintenance.exe: $execute"
    }
    $arguments = @('run')
    $workingDirectory = Split-Path -Parent $execute
}
else {
    $builtExe = Join-Path $projectRoot 'dist\maintenance.exe'
    $venvPython = Join-Path $projectRoot '.venv\Scripts\python.exe'
    if (Test-Path -LiteralPath $builtExe) {
        $execute = $builtExe
        $arguments = @('run')
        $workingDirectory = Split-Path -Parent $builtExe
    }
    elseif (Test-Path -LiteralPath $venvPython) {
        $execute = $venvPython
        $arguments = @('-m', 'maintenance', 'run')
        $workingDirectory = $projectRoot
    }
    else {
        throw 'Agente não encontrado. Gere o executável (scripts\build_exe.ps1), crie o ambiente virtual .venv ou informe -AgentPath.'
    }
}

if ($ConfigPath) {
    $configFile = (Resolve-Path -LiteralPath $ConfigPath).ProviderPath
    if ($configFile.Contains('"')) {
        throw 'ConfigPath contém caracteres inválidos.'
    }
    $arguments += @('--config', ('"{0}"' -f $configFile))
}
$argumentLine = $arguments -join ' '

# --- Definição da tarefa ------------------------------------------------------------
$time = [datetime]::ParseExact($At, 'HH:mm', [Globalization.CultureInfo]::InvariantCulture)
if ($Schedule -eq 'Daily') {
    $trigger = New-ScheduledTaskTrigger -Daily -At $time
    $scheduleText = "diariamente às $At"
}
else {
    $trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek $DayOfWeek -At $time
    $scheduleText = "semanalmente ($DayOfWeek) às $At"
}

if ($RunAs -eq 'System') {
    $account = 'SYSTEM'
    $principal = New-ScheduledTaskPrincipal -UserId 'SYSTEM' -LogonType ServiceAccount -RunLevel Highest
}
else {
    $account = [Security.Principal.WindowsIdentity]::GetCurrent().Name
    # Interactive: executa apenas com o usuário logado; nenhuma senha é armazenada.
    $principal = New-ScheduledTaskPrincipal -UserId $account -LogonType Interactive -RunLevel Highest
}

$action = New-ScheduledTaskAction -Execute $execute -Argument $argumentLine -WorkingDirectory $workingDirectory
$settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Hours $ExecutionTimeLimitHours)
$description = 'Windows Maintenance Agent: manutenção preventiva (Google Chrome, Windows Update ' +
    'e limpeza de temporários). Não reinicia o computador. Logs na pasta logs do agente.'
$task = New-ScheduledTask -Action $action -Trigger $trigger -Principal $principal `
    -Settings $settings -Description $description

$existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($existing -and -not $Force) {
    throw "A tarefa '$TaskName' já existe. Use -Force para substituí-la."
}

Write-Host "Tarefa:       $TaskName"
Write-Host "Comando:      `"$execute`" $argumentLine"
Write-Host "Pasta:        $workingDirectory"
Write-Host "Agendamento:  $scheduleText (executa ao ligar se o horário for perdido)"
Write-Host "Conta:        $account (privilégios mais altos)"
Write-Host "Tempo limite: $ExecutionTimeLimitHours h"

if ($PSCmdlet.ShouldProcess($TaskName, 'Registrar tarefa agendada')) {
    if (-not (Test-IsElevated)) {
        throw 'Execute este script em um PowerShell elevado ("Executar como administrador"). O script não contorna o UAC.'
    }
    Register-ScheduledTask -TaskName $TaskName -InputObject $task -Force:$Force | Out-Null
    Write-Host ''
    Write-Host "Tarefa '$TaskName' registrada."
    Write-Host "Execução manual de teste: Start-ScheduledTask -TaskName '$TaskName'"
}
