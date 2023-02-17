$inputPath = "C:\input"
$outputPath = "C:\output"

Get-ChildItem -Path $inputPath -Recurse | ForEach-Object {
    if ($_.PSIsContainer -eq $false) {
        $relativePath = $_.DirectoryName.Substring($inputPath.Length + 1)
        $outputFolder = Join-Path $outputPath $relativePath
        New-Item -ItemType Directory -Force -Path $outputFolder | Out-Null
        $ext = $_.Extension
        ffmpeg -i $_.FullName -c:v copy -c:a copy "$outputFolder\$($_.BaseName)$ext"
    }
}
