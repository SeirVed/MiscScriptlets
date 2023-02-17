$inputFile = "C:\path\to\input\video.mp4"
$outputFolder = "C:\path\to\output\folder"
$startTime = "00:00:10"   # Replace with the desired start time in HH:MM:SS format
$endTime = "00:00:20"     # Replace with the desired end time in HH:MM:SS format

New-Item -ItemType Directory -Force -Path $outputFolder | Out-Null
$ffprobeOutput = ffmpeg -i $inputFile -show_entries frame=pkt_pts_time,pict_type -of csv -select_streams v:0 -v quiet -show_entries frame=pkt_pts_time,pict_type -of csv=p=0
$lastKeyframeTime = ($ffprobeOutput | ConvertFrom-Csv | Where-Object { $_.pict_type -eq "I" -and $_.pkt_pts_time -lt $endTime } | Select-Object -Last 1).pkt_pts_time
if ($lastKeyframeTime -eq $null) {
    $lastKeyframeTime = $endTime
}
$extendedEndTime = [TimeSpan]::Parse($lastKeyframeTime).ToString("hh\:mm\:ss\.fff")

ffmpeg -i $inputFile -ss $startTime -to $extendedEndTime -c:v copy -c:a copy "$outputFolder\output.mp4"
ffmpeg -i "$outputFolder\output.mp4" -vsync 0 "$outputFolder\frame-%d.png"
