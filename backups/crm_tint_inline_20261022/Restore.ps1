param([switch]$Apply)
$ErrorActionPreference = 'Stop'
$moduleRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../..'))
$manifestPath = Join-Path $PSScriptRoot 'applied_hashes.json'
if (-not (Test-Path -LiteralPath $manifestPath)) {
    throw 'Falta el registro de hashes; no se restauró ningún archivo.'
}
$entries = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json
foreach ($entry in $entries) {
    $target = [IO.Path]::GetFullPath((Join-Path $moduleRoot $entry.path))
    if (-not $target.StartsWith($moduleRoot + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) {
        throw 'Ruta fuera del módulo; restauración detenida.'
    }
    if ((Get-FileHash -LiteralPath $target -Algorithm SHA256).Hash -ne $entry.hash) {
        throw "Cambios posteriores en $($entry.path). Se requiere reversión selectiva; no se restauró ningún archivo."
    }
}
if (-not $Apply) {
    Write-Output 'Verificación correcta. Antes de restaurar: desactivar la vista CRM de entintados y detener Odoo. Usar -Apply para restaurar los seis archivos.'
    return
}
foreach ($entry in $entries) {
    Copy-Item -LiteralPath (Join-Path $PSScriptRoot $entry.path) -Destination (Join-Path $moduleRoot $entry.path)
}
Write-Output 'Restaurados solo los archivos anteriores a la integración CRM. Reiniciar Odoo y actualizar Entintados PDV.'
