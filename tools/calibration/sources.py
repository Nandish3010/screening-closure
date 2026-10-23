"""The external datasets used by the calibration fits (ticket B18): one place for URL, licence and local path.

Downloaded files are UNTRUSTED input. They live only under data/external/<source>/ (gitignored), are never executed,
and are read only by the scripts in this directory, run as `python3 -I` (isolated mode: no cwd or site imports).
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
EXTERNAL = ROOT / "data" / "external"
MANIFEST_LOCAL = EXTERNAL / "MANIFEST.json"      # not committed (data/ is ignored)
MANIFEST_COMMITTED = ROOT / "tools" / "calibration" / "manifest.json"   # committed copy

OGL = "Open Government Licence v3.0 (https://www.nationalarchives.gov.uk/doc/open-government-licence/version/3/)"

SOURCES = [
    {
        "id": "phs",
        "dir": "phs",
        "file": "2025-03-04-bowel-screening-kpi-report.xlsx",
        "url": "https://publichealthscotland.scot/media/31821/2025-03-04-bowel-screening-kpi-report.xlsx",
        "title": "Public Health Scotland, Scottish Bowel Screening Programme KPI report (published 2025-03-04)",
        "licence": OGL,
        "licence_evidence": "Statement 'Open Government Licence v3.0' on https://publichealthscotland.scot/publications/scottish-bowel-screening-programme-statistics/ (checked 2026-10-18)",
        "attribution": "Contains public sector information licensed under the Open Government Licence v3.0. Source: Public Health Scotland.",
    },
    {
        "id": "nhs-bcsp",
        "dir": "nhs-bcsp",
        "file": "Bowel_Cancer_Screening_Annual_Report_Tables_2024-25.xlsx",
        "url": "https://assets.publishing.service.gov.uk/media/6a4e6212f79c9db2eb438f07/Bowel_Cancer_Screening_Annual_Report_Tables_2024-25.xlsx",
        "title": "NHS England, Bowel cancer screening annual report 2024 to 2025, tables",
        "licence": OGL,
        "licence_evidence": "'All content is available under the Open Government Licence v3.0, except where otherwise stated' on https://www.gov.uk/government/statistics/bowel-cancer-screening-annual-report-2024-to-2025 (checked 2026-10-18)",
        "attribution": "Contains public sector information licensed under the Open Government Licence v3.0. Source: NHS England.",
    },
    {
        "id": "nhs-dm01",
        "dir": "nhs-dm01",
        "file": "Monthly-Diagnostics-Provider-March-2026_5WSGC.xls",
        "url": "https://www.england.nhs.uk/statistics/wp-content/uploads/sites/2/2026/09/Monthly-Diagnostics-Provider-March-2026_5WSGC.xls",
        "title": "NHS England, Monthly Diagnostics (DM01), Provider workbook, March 2026 (revised 10.09.26)",
        "licence": OGL,
        "licence_evidence": "NHS England statistics pages are published under the Open Government Licence v3.0 (per the ticket; the page was fetched in a browser on 2026-10-18 and lists the file)",
        "attribution": "Contains public sector information licensed under the Open Government Licence v3.0. Source: NHS England.",
    },
    {
        "id": "openml-noshow",
        "dir": "openml-noshow",
        "file": "Medical-Appointment-No-Shows.arff",
        "url": "https://openml.org/data/v1/download/22102264/Medical-Appointment-No-Shows.arff",
        "title": "Medical Appointment No Shows (OpenML dataset 43439; Vitoria, Brazil, 2016; 110,527 appointments; uploaded from Kaggle, J. Hoppen / Aquarela)",
        "licence": "CC BY-NC-SA 4.0 (non-commercial, share-alike)",
        "licence_evidence": "OpenML API dataset description field `licence` = 'CC BY-NC-SA 4.0' (https://www.openml.org/api/v1/json/data/43439, checked 2026-10-18); the Kaggle page shows no licence (third-party statement, unconfirmed at source)",
        "attribution": "Medical Appointment No Shows, J. Hoppen / Aquarela Advanced Analytics, via OpenML dataset 43439, CC BY-NC-SA 4.0. Only fitted coefficients derived from it are published, never the data.",
    },
    {
        "id": "bca-nbcsp2026",
        "dir": "bca-nbcsp2026",
        "file": "nbcsp-2026-summary.html",
        "url": "https://www.bowelcanceraustralia.org/?p=15157",
        "title": "Bowel Cancer Australia, summary of the AIHW NBCSP monitoring report 2026 (secondary source; AIHW itself is blocked)",
        "licence": "Website content, no open licence stated; only the published figures (facts) are extracted, the page is not republished",
        "licence_evidence": "No licence statement found on the page (checked 2026-10-23); facts only",
        "attribution": "Figures from the AIHW National Bowel Cancer Screening Program monitoring report 2026 as reported by Bowel Cancer Australia.",
    },
]

# Not downloaded: recorded so the manifest is complete about what was attempted.
BLOCKED = [
    {
        "id": "aihw-nbcsp-2024",
        "page": "https://www.aihw.gov.au/reports/cancer-screening/nbcsp-monitoring-2024/data",
        "licence": "CC BY 4.0 (per the ticket; could not be confirmed on the page)",
        "reason": "The AIHW site answered every request (curl with a browser User-Agent: HTTP 403; browser pane: Cloudflare 'Verify you are human' challenge). Completing a bot-detection challenge is not done by this project, so nothing was downloaded. The owner can place the XLSX by hand in data/external/aihw/ and re-run `make calibrate-fit`. Retried on 2026-10-23 (ticket B20, plain curl and WebFetch): HTTP 403 again, nothing bypassed; a secondary published summary is used instead (id bca-nbcsp2026) and graded as secondary.",
    },
]
