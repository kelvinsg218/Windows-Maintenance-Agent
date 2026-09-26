#Requires -Version 5.1
<#
.SYNOPSIS
    Remove a tarefa agendada do Windows Maintenance Agent.

.DESCRIPTION
    Remove apenas a tarefa do Agendador de Tarefas. Logs, relatórios, histórico e
    configuração do agente são preservados.

    O script NÃO se auto-eleva e NÃO contorna o UAC: execute-o em um PowerShell aberto
    com "Executar como administrador". Use -WhatIf para simular.

.PARAMETER TaskName
    Nome da tarefa (padrão: "Windows Maintenance Agent").

.EXAMPLE
    .\scripts\remove_task.ps1 -WhatIf

.EXAMPLE
    .\scripts\remove_task.ps1
#>
[CmdletBinding(SupportsShouldProcess = $true)]
param(
    [ValidateNotNullOrEmpty()]
    [string]$TaskName = 'Windows Maintenance Agent'
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Test-IsElevated {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($identity)
    return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

$task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if (-not $task) {
    Write-Host "Tarefa '$TaskName' não encontrada. Nada a remover."
    return
}

if ($PSCmdlet.ShouldProcess($TaskName, 'Remover tarefa agendada')) {
    if (-not (Test-IsElevated)) {
        throw 'Execute este script em um PowerShell elevado ("Executar como administrador"). O script não contorna o UAC.'
    }
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Write-Host "Tarefa '$TaskName' removida. Logs, relatórios e histórico foram preservados."
}
