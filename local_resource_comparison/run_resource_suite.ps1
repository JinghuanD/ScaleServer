param(
    [ValidateSet('stable','step')][string]$Profile = 'stable',
    [string]$Batch = (Get-Date -Format 'yyyyMMdd_HHmmss')
)
$ErrorActionPreference = 'Stop'
Set-Location (Split-Path $PSScriptRoot -Parent)
$plans = @(
    @('polling','p1',1), @('event','e',1),
    @('event','e',2), @('polling','p1',2),
    @('polling','p1',3), @('event','e',3)
)
foreach ($plan in $plans) {
    docker start app1
    if ($LASTEXITCODE -ne 0) { throw 'Cannot start app1' }
    docker stop app2 app3
    if ($LASTEXITCODE -ne 0) { throw 'Cannot reset app2/app3' }
    $runId = 'resource-{0}-{1}-{2:D2}-{3}' -f $Profile,$plan[1],[int]$plan[2],$Batch
    $trialArgs = @('.\local_resource_comparison\run_trial.py','--arm',$plan[0],'--run-id',$runId,'--profile',$Profile,'--low-rate','1','--poll-period','1','--manage-lb')
    if ($Profile -eq 'step') { $trialArgs += @('--high-rate','5') }
    python @trialArgs
    if ($LASTEXITCODE -ne 0) { throw "Trial failed: $runId. Inspect logs before continuing." }
    python .\local_resource_comparison\analyze_resources.py ".\data\local_resource_comparison\$runId"
    if ($LASTEXITCODE -ne 0) { throw "Resource analysis failed: $runId" }
}
Write-Host "Completed batch: $Batch ($Profile)"
