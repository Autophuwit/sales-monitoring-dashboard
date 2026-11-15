<#
  Monthly / daily update in one command:  load sales -> load plans -> export -> publish.

    .\update.ps1                     # this month, publish to the website
    .\update.ps1 -Month 202608       # a given month (targets / game plans / close gap / market share)
    .\update.ps1 -SkipPlan           # sales only (e.g. a new daily sales file)
    .\update.ps1 -NoPush             # update the local data file but do not publish

  Stops at the first failing step, so a broken load is never published.
  Needs: SQL Server Express running, Python packages from requirements.txt, git logged in to GitHub.
#>
param(
  [string]$Month = (Get-Date -Format "yyyyMM"),
  [switch]$SkipPlan,
  [switch]$NoPush
)
$ErrorActionPreference = "Continue"   # native tools write progress to stderr; failures are checked by exit code
Set-Location $PSScriptRoot

function Step($title, [scriptblock]$cmd) {
  Write-Host ""
  Write-Host "== $title" -ForegroundColor Cyan
  & $cmd
  if ($LASTEXITCODE -ne 0) { Write-Host "FAILED: $title (exit $LASTEXITCODE). Nothing was published." -ForegroundColor Red; exit $LASTEXITCODE }
}

if ($Month -notmatch '^\d{6}$') { throw "Month must look like 202608" }

Step "1/4 Load sales files" { python etl\load_sales.py }
if (-not $SkipPlan) { Step "2/4 Load plans, targets and market share for $Month" { python etl\load_plan.py --month $Month } }
else { Write-Host "`n== 2/4 Skipped (sales only)" -ForegroundColor DarkGray }
Step "3/4 Export dashboard data" { python etl\export_html.py }

if ($NoPush) { Write-Host "`n== 4/4 Not published (-NoPush). Open index.html to check." -ForegroundColor Yellow; exit 0 }

Write-Host "`n== 4/4 Publish" -ForegroundColor Cyan
git add data/dashboard-data.js
git diff --cached --quiet -- data/dashboard-data.js
if ($LASTEXITCODE -eq 0) { Write-Host "Data unchanged - nothing to publish." -ForegroundColor Yellow; exit 0 }
Step "commit" { git commit -q -m "Data update $Month ($(Get-Date -Format 'yyyy-MM-dd HH:mm'))" }
Step "push" { git push -q }
Write-Host "`nDone. The website updates in about 1-2 minutes." -ForegroundColor Green
