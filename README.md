## Crack Pipeline MVP

## Scope
- Method: `ssim_ref` (MVP) + `diff_prev` baseline
- ROI:
  - `fixed`: use configured ROI directly
  - `auto_per_video`: per-video ROI via ORB feature matching + local refinement (recommended)
- Outputs: `meta.json`, `score_curve.csv`, `segments.json`, `frames/`, `crops/`, `manifest.csv`, `preview.jpg`, `_run/index.html`

## Install
```powershell
python -m pip install -r requirements.txt
```

## Pilot (5 videos)
```powershell
run_pilot.bat
```
Equivalent command:
```powershell
python crack_analyze.py --config config.pilot.yaml --max_videos 5
```

## Full run
```powershell
run_all.bat
```
Current full-run config: `config.rawdata.autoroi.yaml`

If extracted frames miss part of the crack evolution, try:
```powershell
python crack_analyze.py --config config.rawdata.fullcrack.yaml
```
or:
```powershell
run_fullcrack.bat
```

## CLI
```powershell
python crack_analyze.py --config config.yaml [--input_dir ...] [--output_dir ...] \
  [--method ssim_ref|diff_prev|flow] [--roi x,y,w,h] [--dry_run] \
  [--report_html|--no-report_html] [--fail_fast] [--max_videos N]
```

## Tuning tips
- False positives high: shrink ROI; raise `threshold.q` (`0.98 -> 0.995`); increase `smooth_win` (`7 -> 11`).
- Misses high: enlarge ROI; lower `threshold.q` (`0.98 -> 0.95`); increase `coarse_fps` (`6 -> 8~10`); increase `segment_len_sec` (`8 -> 12~20`).
- For complete crack process coverage, use `segment_strategy: run` with `run_pre_pad_sec/run_post_pad_sec` to expand from threshold runs.
- Short videos (`duration < segment_len_sec`) are auto-clamped.
- `ssim_ref_n_ref` controls reference robustness (`1` = first frame only, `5` = median of first 5 sampled frames).
- If threshold runs are too few, pipeline can auto-supplement to `topk` (`fill_to_topk: true`).
- `auto_per_video` key params:
  - `roi.auto.min_inliers`: lower if angle changes are large and matching fails
  - `roi.auto.search_ratio/search_steps/scales`: controls local recentering strength
  - `out/_run/roi_overrides.json`: inspect per-video ROI, source, match stats

## Notes
- `flow` is reserved for v2 and intentionally blocked in MVP.
- Current backend is OpenCV, so ffmpeg/ffprobe are not required to be in PATH.
