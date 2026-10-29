$ErrorActionPreference = 'Stop'
$fixtureParent = [IO.Path]::GetFullPath('E:/QTrade-temp')
$newFixture = Join-Path $fixtureParent ('p0-bounded-git-fixture-' + [DateTime]::UtcNow.ToString('yyyyMMddTHHmmssZ') + '-' + [guid]::NewGuid().ToString('N').Substring(0,8))
$newFixtureAbsolute = [IO.Path]::GetFullPath($newFixture)
if (-not $newFixtureAbsolute.StartsWith($fixtureParent + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) { throw 'Fixture path must stay inside E:/QTrade-temp' }
New-Item -ItemType Directory -Path $newFixtureAbsolute | Out-Null
Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'run_fixture.py') -Destination (Join-Path $newFixtureAbsolute 'run_fixture.py')
& python -B (Join-Path $newFixtureAbsolute 'run_fixture.py')
if ($LASTEXITCODE -ne 0) { throw "Fixture failed; preserve raw evidence in $newFixtureAbsolute" }
Write-Output $newFixtureAbsolute
