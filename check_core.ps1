$path = "D:\Project\pygame-piano-animator\corefull.out"
if (Test-Path $path) {
  $s = Get-Content $path -Raw
  Write-Host $s.Length
  Write-Host $s.Substring([Math]::Max(0,$s.Length-200))
} else {
  Write-Host "no"
}
