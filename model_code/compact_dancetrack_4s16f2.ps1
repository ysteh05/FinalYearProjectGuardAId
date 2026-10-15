param([switch]$PlanOnly, [switch]$ManifestOnly)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

if ($PlanOnly -and $ManifestOnly) {
    throw 'PlanOnly and ManifestOnly cannot be used together.'
}

$workspace = (Resolve-Path -LiteralPath '.').Path
$expectedRoot = Join-Path $workspace 'GuardAId Video Classification Model Dataset\DanceTrack'
$root = (Resolve-Path -LiteralPath $expectedRoot).Path
if (-not [System.StringComparer]::OrdinalIgnoreCase.Equals($root, $expectedRoot)) {
    throw "Resolved dataset path did not match the authorized target: $root"
}

$manifestPath = Join-Path $root 'dancetrack_4s16f2_frame_manifest.json'
$reportPath = Join-Path $root 'dancetrack_4s16f2_compaction_report.json'
if ((Test-Path -LiteralPath $manifestPath) -or (Test-Path -LiteralPath $reportPath)) {
    throw 'A DanceTrack compaction manifest/report already exists; refusing to overwrite it.'
}

$splitByShard = @{
    train1 = 'train'
    train2 = 'train'
    val    = 'val'
    test1  = 'test'
    test2  = 'test'
}
$expectedShardCounts = @{ train1 = 20; train2 = 20; val = 25; test1 = 20; test2 = 15 }
$expectedSplitCounts = @{
    train = @{ sequences = 40; windows = 1040; original_frames = 41796; kept_frames = 8960; removed_frames = 32836 }
    val   = @{ sequences = 25; windows = 631;  original_frames = 25508; kept_frames = 5440; removed_frames = 20068 }
    test  = @{ sequences = 35; windows = 960;  original_frames = 38551; kept_frames = 8240; removed_frames = 30311 }
}

$sequenceInfos = @(Get-ChildItem -LiteralPath $root -Filter 'seqinfo.ini' -File -Recurse)
if ($sequenceInfos.Count -ne 100) {
    throw "Expected exactly 100 DanceTrack sequences, found $($sequenceInfos.Count). No images have been changed."
}

$sequencePlans = [System.Collections.Generic.List[object]]::new()
$shardCounts = @{ train1 = 0; train2 = 0; val = 0; test1 = 0; test2 = 0 }
$splitStats = @{
    train = [ordered]@{ sequences = 0; windows = 0; original_frames = 0; kept_frames = 0; removed_frames = 0; original_bytes = [long]0; kept_bytes = [long]0; removed_bytes = [long]0 }
    val   = [ordered]@{ sequences = 0; windows = 0; original_frames = 0; kept_frames = 0; removed_frames = 0; original_bytes = [long]0; kept_bytes = [long]0; removed_bytes = [long]0 }
    test  = [ordered]@{ sequences = 0; windows = 0; original_frames = 0; kept_frames = 0; removed_frames = 0; original_bytes = [long]0; kept_bytes = [long]0; removed_bytes = [long]0 }
}
$totalOriginalBytes = [long]0
$totalKeptBytes = [long]0
$totalOriginalFrames = 0
$totalKeptFrames = 0
$totalRemovedFrames = 0
$totalWindows = 0
$rootPrefix = $root.TrimEnd('\') + '\'

foreach ($seqInfo in $sequenceInfos) {
    $seqDir = $seqInfo.Directory.FullName
    $seqRelative = $seqDir.Substring($rootPrefix.Length)
    $shard = $seqRelative.Split('\')[0]
    if (-not $splitByShard.ContainsKey($shard)) {
        throw "Unexpected DanceTrack shard '${shard}' at '${seqRelative}'. No images have been changed."
    }
    $split = $splitByShard[$shard]
    $shardCounts[$shard]++

    $ini = [System.IO.File]::ReadAllText($seqInfo.FullName)
    $fpsMatch = [regex]::Match($ini, '(?m)^\s*frameRate\s*=\s*([^\r\n#]+)')
    $lengthMatch = [regex]::Match($ini, '(?m)^\s*seqLength\s*=\s*([^\r\n#]+)')
    $imDirMatch = [regex]::Match($ini, '(?m)^\s*imDir\s*=\s*([^\r\n#]+)')
    if (-not $fpsMatch.Success -or -not $lengthMatch.Success -or -not $imDirMatch.Success) {
        throw "Missing frameRate/seqLength/imDir in '${seqRelative}\seqinfo.ini'. No images have been changed."
    }
    $fps = [double]::Parse($fpsMatch.Groups[1].Value.Trim(), [Globalization.CultureInfo]::InvariantCulture)
    $seqLength = [int]::Parse($lengthMatch.Groups[1].Value.Trim(), [Globalization.CultureInfo]::InvariantCulture)
    if ($fps -ne 20.0 -or $seqLength -le 0) {
        throw "Unexpected FPS or sequence length in '${seqRelative}': fps=$fps, seqLength=$seqLength. No images have been changed."
    }

    $imageRootCandidate = Join-Path $seqDir $imDirMatch.Groups[1].Value.Trim()
    $imageRoot = (Resolve-Path -LiteralPath $imageRootCandidate).Path
    if (-not $imageRoot.StartsWith($seqDir.TrimEnd('\') + '\', [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Image directory escapes sequence folder: '${imageRoot}'. No images have been changed."
    }
    $imageFiles = @(Get-ChildItem -LiteralPath $imageRoot -Filter '*.jpg' -File)
    if ($imageFiles.Count -ne $seqLength) {
        throw "${seqRelative}: seqLength=$seqLength but found $($imageFiles.Count) JPG files. No images have been changed."
    }
    $filesByFrame = @{}
    foreach ($imageFile in $imageFiles) {
        $frameMatch = [regex]::Match($imageFile.Name, '^(\d+)\.jpg$', [System.Text.RegularExpressions.RegexOptions]::IgnoreCase)
        if (-not $frameMatch.Success) {
            throw "Unexpected JPG filename '${imageFile.FullName}'. No images have been changed."
        }
        $frameNumber = [int]::Parse($frameMatch.Groups[1].Value, [Globalization.CultureInfo]::InvariantCulture)
        if ($filesByFrame.ContainsKey($frameNumber)) {
            throw "Duplicate frame number $frameNumber in '${seqRelative}'. No images have been changed."
        }
        $filesByFrame[$frameNumber] = $imageFile
    }
    for ($frameNumber = 1; $frameNumber -le $seqLength; $frameNumber++) {
        if (-not $filesByFrame.ContainsKey($frameNumber)) {
            throw "Missing expected frame $frameNumber in '${seqRelative}'. No images have been changed."
        }
    }

    $duration = $seqLength / $fps
    $intervals = [System.Collections.Generic.List[object]]::new()
    if ($duration -lt 4.0) {
        $intervals.Add([pscustomobject]@{ Start = 0.0; End = $duration; IsShort = $true })
    }
    else {
        $windowStart = 0.0
        while (($windowStart + 4.0) -le ($duration + 1e-9)) {
            $roundedStart = [Math]::Round($windowStart, 9)
            $intervals.Add([pscustomobject]@{ Start = $roundedStart; End = [Math]::Round($roundedStart + 4.0, 9); IsShort = $false })
            $windowStart += 2.0
        }
        $tailStart = [Math]::Max(0.0, $duration - 4.0)
        if ($intervals.Count -eq 0 -or $tailStart -gt ($intervals[$intervals.Count - 1].Start + 1e-6)) {
            $intervals.Add([pscustomobject]@{ Start = $tailStart; End = $duration; IsShort = $false })
        }
    }

    $keepNumbers = [System.Collections.Generic.HashSet[int]]::new()
    $windowRecords = [System.Collections.Generic.List[object]]::new()
    foreach ($interval in $intervals) {
        $sampleFrameNumbers = [System.Collections.Generic.List[int]]::new()
        $sampleTimes = [System.Collections.Generic.List[double]]::new()
        $samplePaths = [System.Collections.Generic.List[string]]::new()
        for ($sampleIndex = 0; $sampleIndex -lt 16; $sampleIndex++) {
            if ($interval.IsShort) {
                $sampleTime = (($sampleIndex + 0.5) * $duration / 16.0)
            }
            else {
                $sampleTime = ($interval.Start + ($sampleIndex + 0.5) * 4.0 / 16.0)
            }
            $zeroBasedFrame = [int][Math]::Floor($sampleTime * $fps)
            $zeroBasedFrame = [Math]::Max(0, [Math]::Min($seqLength - 1, $zeroBasedFrame))
            $frameNumber = $zeroBasedFrame + 1
            [void]$keepNumbers.Add($frameNumber)
            $sampleFrameNumbers.Add($frameNumber)
            $sampleTimes.Add([double]$sampleTime)
            $samplePaths.Add($filesByFrame[$frameNumber].FullName.Substring($rootPrefix.Length))
        }
        $windowRecords.Add([ordered]@{
            start_seconds = [double]$interval.Start
            end_seconds = [double]$interval.End
            sample_times_seconds = @($sampleTimes.ToArray())
            source_frame_numbers_1based = @($sampleFrameNumbers.ToArray())
            source_image_paths = @($samplePaths.ToArray())
        })
    }

    $keptSorted = @($keepNumbers | Sort-Object)
    $originalBytes = [long]0
    $keptBytes = [long]0
    $removePaths = [System.Collections.Generic.List[string]]::new()
    foreach ($frameNumber in 1..$seqLength) {
        $file = $filesByFrame[$frameNumber]
        $originalBytes += [long]$file.Length
        if ($keepNumbers.Contains($frameNumber)) {
            $keptBytes += [long]$file.Length
        }
        else {
            $removePaths.Add($file.FullName)
        }
    }
    $removedCount = $seqLength - $keepNumbers.Count
    $sequencePlans.Add([pscustomobject]@{
        RelativePath = $seqRelative
        Split = $split
        Shard = $shard
        ImageRoot = $imageRoot
        OriginalFrames = $seqLength
        KeepNumbers = $keptSorted
        RemovePaths = @($removePaths.ToArray())
        Windows = @($windowRecords.ToArray())
        OriginalBytes = $originalBytes
        KeptBytes = $keptBytes
        RemovedFrames = $removedCount
    })

    $stats = $splitStats[$split]
    $stats.sequences++
    $stats.windows += $intervals.Count
    $stats.original_frames += $seqLength
    $stats.kept_frames += $keepNumbers.Count
    $stats.removed_frames += $removedCount
    $stats.original_bytes += $originalBytes
    $stats.kept_bytes += $keptBytes
    $stats.removed_bytes += ($originalBytes - $keptBytes)
    $totalOriginalFrames += $seqLength
    $totalKeptFrames += $keepNumbers.Count
    $totalRemovedFrames += $removedCount
    $totalWindows += $intervals.Count
    $totalOriginalBytes += $originalBytes
    $totalKeptBytes += $keptBytes
}

foreach ($shard in $expectedShardCounts.Keys) {
    if ($shardCounts[$shard] -ne $expectedShardCounts[$shard]) {
        throw "Shard '${shard}' had $($shardCounts[$shard]) sequences; expected $($expectedShardCounts[$shard]). No images have been changed."
    }
}
if ($totalOriginalFrames -ne 105855 -or $totalKeptFrames -ne 22640 -or $totalRemovedFrames -ne 83215 -or $totalWindows -ne 2631) {
    throw "Computed plan did not match the verified 4S16F2 totals (original=$totalOriginalFrames, kept=$totalKeptFrames, removed=$totalRemovedFrames, windows=$totalWindows). No images have been changed."
}
foreach ($split in @('train', 'val', 'test')) {
    foreach ($key in @('sequences', 'windows', 'original_frames', 'kept_frames', 'removed_frames')) {
        if ($splitStats[$split][$key] -ne $expectedSplitCounts[$split][$key]) {
            throw "Computed $split $key did not match the verified split plan. No images have been changed."
        }
    }
}

$sequenceManifest = [System.Collections.Generic.List[object]]::new()
foreach ($plan in $sequencePlans) {
    $sequenceManifest.Add([ordered]@{
        sequence_path = $plan.RelativePath
        official_split = $plan.Split
        source_shard = $plan.Shard
        fps = 20
        original_frame_count = $plan.OriginalFrames
        retained_unique_frame_count = $plan.KeepNumbers.Count
        removed_frame_count = $plan.RemovedFrames
        retained_frame_numbers_1based = @($plan.KeepNumbers)
        windows = @($plan.Windows)
    })
}
$summary = [ordered]@{
    sequence_count = $sequencePlans.Count
    window_count = $totalWindows
    original_jpg_count = $totalOriginalFrames
    retained_jpg_count = $totalKeptFrames
    removed_jpg_count = $totalRemovedFrames
    original_jpg_bytes = $totalOriginalBytes
    retained_jpg_bytes = $totalKeptBytes
    estimated_reclaimed_bytes = ($totalOriginalBytes - $totalKeptBytes)
    by_split = $splitStats
}
$manifest = [ordered]@{
    schema_version = 1
    created_utc = [DateTime]::UtcNow.ToString('o')
    dataset_root = $root
    protocol = '4S16F2'
    sampling = [ordered]@{
        fps = 20
        window_seconds = 4
        stride_seconds = 2
        frames_per_window = 16
        index_rule = 'floor(sample_time_seconds * 20), clamped to [0, frame_count - 1]'
        short_clip_rule = 'For clips shorter than 4 seconds, place 16 sample centers uniformly across the natural clip duration.'
        tail_rule = 'Use full 4-second windows at 2-second stride and add one end-aligned tail window when not already covered.'
    }
    split_mapping = [ordered]@{ train1 = 'train'; train2 = 'train'; val = 'val'; test1 = 'test'; test2 = 'test' }
    summary = $summary
    sequences = @($sequenceManifest.ToArray())
}
$manifestJson = ConvertTo-Json -InputObject $manifest -Depth 16
if ($PlanOnly) {
    Write-Output "PLAN ONLY: no files were written or deleted."
    Write-Output ("Sequences={0}; windows={1}; original JPGs={2}; retained={3}; to remove={4}; expected reclaim={5:N2} GiB" -f $sequencePlans.Count, $totalWindows, $totalOriginalFrames, $totalKeptFrames, $totalRemovedFrames, (($totalOriginalBytes - $totalKeptBytes) / 1GB))
    foreach ($split in @('train', 'val', 'test')) {
        $stats = $splitStats[$split]
        Write-Output ("{0}: sequences={1}, windows={2}, retained={3}, remove={4}" -f $split, $stats.sequences, $stats.windows, $stats.kept_frames, $stats.removed_frames)
    }
    exit 0
}
$tempManifestPath = $manifestPath + '.tmp-' + [Guid]::NewGuid().ToString('N')
[System.IO.File]::WriteAllText($tempManifestPath, $manifestJson, [System.Text.UTF8Encoding]::new($false))
Move-Item -LiteralPath $tempManifestPath -Destination $manifestPath
$manifestCheck = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json
if ($manifestCheck.summary.retained_jpg_count -ne 22640 -or $manifestCheck.sequences.Count -ne 100) {
    throw 'Written frame manifest failed integrity verification; no images have been changed.'
}

if ($ManifestOnly) {
    $report = [ordered]@{
        completed_utc = [DateTime]::UtcNow.ToString('o')
        status = 'manifest_restored_without_compaction'
        manifest = 'dancetrack_4s16f2_frame_manifest.json'
        summary = $summary
        verification = [ordered]@{
            sequence_count = $sequencePlans.Count
            all_required_4s16f2_frame_numbers_exist = $true
            source_jpgs_written = $false
            source_jpgs_deleted = $false
        }
    }
    $reportJson = ConvertTo-Json -InputObject $report -Depth 12
    $tempReportPath = $reportPath + '.tmp-' + [Guid]::NewGuid().ToString('N')
    [System.IO.File]::WriteAllText($tempReportPath, $reportJson, [System.Text.UTF8Encoding]::new($false))
    Move-Item -LiteralPath $tempReportPath -Destination $reportPath
    Write-Output "MANIFEST ONLY: verified plan written: $manifestPath"
    Write-Output "Preserved all $totalOriginalFrames source JPGs; no images were written or deleted."
    Write-Output "Report: $reportPath"
    exit 0
}

Write-Output "Verified plan written: $manifestPath"
Write-Output "Plan: $totalOriginalFrames originals -> $totalKeptFrames retained, $totalRemovedFrames to remove; $totalWindows windows."
$deletedSoFar = 0
$sequenceIndex = 0
foreach ($plan in $sequencePlans) {
    $sequenceIndex++
    foreach ($path in $plan.RemovePaths) {
        Remove-Item -LiteralPath $path -Force
        $deletedSoFar++
    }
    Write-Output ("[{0}/100] {1} ({2}): kept {3}/{4} JPG, removed {5}; total removed {6}/{7}" -f $sequenceIndex, $plan.RelativePath, $plan.Split, $plan.KeepNumbers.Count, $plan.OriginalFrames, $plan.RemovedFrames, $deletedSoFar, $totalRemovedFrames)
}

foreach ($plan in $sequencePlans) {
    $actual = @(Get-ChildItem -LiteralPath $plan.ImageRoot -Filter '*.jpg' -File)
    if ($actual.Count -ne $plan.KeepNumbers.Count) {
        throw "Post-delete JPG count mismatch in '$($plan.RelativePath)': expected $($plan.KeepNumbers.Count), found $($actual.Count)."
    }
    # Verify retained frame identifiers directly from the manifest-derived plan.
    $actualNumbers = [System.Collections.Generic.HashSet[int]]::new()
    foreach ($file in $actual) {
        $frameMatch = [regex]::Match($file.Name, '^(\d+)\.jpg$', [System.Text.RegularExpressions.RegexOptions]::IgnoreCase)
        if (-not $frameMatch.Success) { throw "Unexpected post-delete file '$($file.FullName)'." }
        [void]$actualNumbers.Add([int]::Parse($frameMatch.Groups[1].Value, [Globalization.CultureInfo]::InvariantCulture))
    }
    foreach ($frameNumber in $plan.KeepNumbers) {
        if (-not $actualNumbers.Contains([int]$frameNumber)) {
            throw "Post-delete retained frame $frameNumber is missing in '$($plan.RelativePath)'."
        }
    }
}

$report = [ordered]@{
    completed_utc = [DateTime]::UtcNow.ToString('o')
    status = 'complete'
    manifest = 'dancetrack_4s16f2_frame_manifest.json'
    summary = $summary
    verification = [ordered]@{
        sequence_count = $sequencePlans.Count
        all_retained_jpg_counts_and_frame_numbers_match_manifest = $true
        non_jpg_files_and_split_layout_modified = $false
    }
}
$reportJson = ConvertTo-Json -InputObject $report -Depth 12
$tempReportPath = $reportPath + '.tmp-' + [Guid]::NewGuid().ToString('N')
[System.IO.File]::WriteAllText($tempReportPath, $reportJson, [System.Text.UTF8Encoding]::new($false))
Move-Item -LiteralPath $tempReportPath -Destination $reportPath

Write-Output "DONE: verified 100 sequences and $totalKeptFrames retained JPGs."
Write-Output ("Estimated reclaimed file space: {0:N2} GiB" -f (($totalOriginalBytes - $totalKeptBytes) / 1GB))
Write-Output "Report: $reportPath"

