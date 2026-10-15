<#
.SYNOPSIS
    Query the semantic model open in Power BI Desktop (local Analysis Services engine) and save DAX results.

.DESCRIPTION
    Windows only, needs Power BI Desktop. Open powerbi/project/SalesPerformance.pbip (with the DataFolder
    parameter pointing at your exports), then run:

        powershell -ExecutionPolicy Bypass -File powerbi\validation\query_model.ps1 -OutFile dax_results.json -Refresh
        python scripts\compare_powerbi_results.py dax_results.json output\powerbi

    -Refresh runs a full refresh of the model through the engine first. The script uses the ADOMD.NET
    client shipped with Power BI Desktop and the port of the most recent local workspace; it reads data
    only (apart from the optional refresh) and never changes the report.
#>
param([string]$OutFile, [switch]$Refresh)
$ErrorActionPreference = "Stop"
Add-Type -Path "C:\Program Files\Microsoft Power BI Desktop\bin\Microsoft.PowerBI.AdomdClient.dll"
$ws = Get-ChildItem "$env:LOCALAPPDATA\Microsoft\Power BI Desktop\AnalysisServicesWorkspaces" -Directory |
    Sort-Object LastWriteTime -Descending | Select-Object -First 1
$port = (Get-Content (Join-Path $ws.FullName "Data\msmdsrv.port.txt") -Raw -Encoding Unicode).Trim()
$conn = New-Object Microsoft.AnalysisServices.AdomdClient.AdomdConnection "Data Source=localhost:$port"
$conn.Open()
$c0 = $conn.CreateCommand()
$c0.CommandText = 'SELECT [CATALOG_NAME] FROM $SYSTEM.DBSCHEMA_CATALOGS'
$r0 = $c0.ExecuteReader(); [void]$r0.Read(); $db = $r0.GetValue(0); $r0.Close()
$result = [ordered]@{ port = $port; database = $db }

function Run-Dax([string]$query) {
    $cmd = $conn.CreateCommand()
    $cmd.CommandText = $query
    $reader = $cmd.ExecuteReader()
    $rows = @()
    while ($reader.Read()) {
        $row = [ordered]@{}
        for ($i = 0; $i -lt $reader.FieldCount; $i++) {
            $v = $reader.GetValue($i)
            if ($v -is [DBNull]) { $v = $null }
            $row[$reader.GetName($i)] = $v
        }
        $rows += [pscustomobject]$row
    }
    $reader.Close()
    return ,$rows
}

if ($Refresh) {
    $cmd = $conn.CreateCommand()
    $cmd.CommandText = '{"refresh": {"type": "full", "objects": [{"database": "' + $db + '"}]}}'
    $sw = [Diagnostics.Stopwatch]::StartNew()
    [void]$cmd.ExecuteNonQuery()
    $result.refresh_seconds = [math]::Round($sw.Elapsed.TotalSeconds, 1)
}

$result.row_counts = Run-Dax @"
EVALUATE ROW(
  "sales_rows", COUNTROWS(Sales), "calendar_rows", COUNTROWS(Calendar), "product_rows", COUNTROWS(Product),
  "quality_rows", COUNTROWS('Quality Checks'), "rejected_rows", COUNTROWS('Rejected Rows'))
"@
$result.totals = Run-Dax @"
EVALUATE ROW(
  "net_sales", [Net Sales], "orders", [Orders], "units", [Units Sold], "customers", [Active Customers],
  "aov", [Average Order Value], "margin", [Gross Margin], "margin_pct", [Gross Margin %],
  "discount_rate", [Discount Rate], "cancellation_rate", [Cancellation Rate],
  "rejected", [Rejected Rows], "checks_passed", [Checks Passed], "checks_failed", [Checks Failed],
  "latest_mom", [Latest Month MoM %], "latest_yoy", [Latest Month YoY %])
"@
$result.monthly = Run-Dax @"
EVALUATE
ADDCOLUMNS(
  SUMMARIZE(Calendar, Calendar[month_key], Calendar[year_month]),
  "net_sales", [Net Sales], "net_sales_pm", [Net Sales PM], "mom", [MoM Growth %],
  "net_sales_py", [Net Sales PY], "yoy", [YoY Growth %], "orders", [Orders], "ytd", [Net Sales YTD])
ORDER BY Calendar[month_key]
"@
$result.categories = Run-Dax @"
EVALUATE ADDCOLUMNS(VALUES(Product[category]), "net_sales", [Net Sales], "share", [Category Share %])
ORDER BY Product[category]
"@
$result.products = Run-Dax @"
EVALUATE ADDCOLUMNS(VALUES(Product[product_name]), "net_sales", [Net Sales], "rank", [Product Rank])
ORDER BY [rank]
"@
$conn.Close()
$result | ConvertTo-Json -Depth 6 | Set-Content -Path $OutFile -Encoding utf8
"written $OutFile"
