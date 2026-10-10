# Reflection (draft, ~100 words)

**What went wrong?** My first model, a logistic regression, was worse than a simple hour-of-day
climatology beyond one hour. It gave the same forecast for every horizon and ignored the drift in
alert frequency. Even the strong 1-hour score of later models mostly came from alerts already in
progress. Extra "regime" features overfitted. The parallel Telegram scraper silently lost 4 of 7 files,
and a pandas time-unit bug zeroed every text feature.

**How did I adjust?** I switched to gradient boosting with neighbour-region and national features and
scored "quiet" moments separately. I also added leakage tests, ID-gap checks and a bootstrap significance test.

**Why is the final version better?** It beats every baseline at all horizons, it is calibrated and
leakage-tested, and it measures honestly what Telegram adds: a small gain at 1–3 hours.
