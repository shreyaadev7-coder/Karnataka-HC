# Karnataka High Court Advocate Cause List Monitor (GitHub Actions)

This version uses the High Court of Karnataka's **Cause List Search** page and its **Advocate-wise** search facility, rather than relying only on PDF URLs.

Target URL:
https://judiciary.karnataka.gov.in/causelistSearch.php

It searches all three benches:
- Bengaluru Bench
- Dharwad Bench
- Kalaburagi Bench

It searches the configured advocate names and variants, using conservative fuzzy/OCR matching, and separates uncertain matches for manual review.

## One-time GitHub setup

1. Create a **Private** GitHub repository.
2. Upload all files in this folder, including `.github/workflows/cause-list-monitor.yml`.
3. Open **Settings → Actions → General** and make sure Actions are allowed for the repository.
4. Open **Actions → Karnataka HC Cause List Monitor → Run workflow** to test it.
5. Thereafter GitHub runs it automatically on the configured schedule.

No Python or software needs to be installed on your Mac.

## Results

The workflow writes:
- `results/latest_matches.xlsx`
- `results/latest_matches.csv`
- `results/latest_matches.md`
- `results/latest_uncertain.xlsx`
- `results/latest_uncertain.csv`
- `results/latest_uncertain.md`

The workflow also shows a summary of confirmed and uncertain matches in the Actions run page.

## Schedule

The default schedule is every 10 minutes on weekdays, offset from the top of the hour. GitHub Actions schedules may occasionally be delayed during periods of high load.

## Notes

The Court page currently says that advocate-wise searches are available from the main Cause List Search and that a date range cannot exceed 7 days. The workflow therefore searches the current date through the next 6 days. Change `DAYS_AHEAD` in the workflow to alter this.

The browser automation intentionally avoids bypassing CAPTCHA or access controls. If the Court site introduces a CAPTCHA or blocks automated browsing, the run will fail visibly in GitHub Actions rather than attempting to evade the control.
