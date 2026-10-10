"""Train on all labelled data and forecast from the latest available hour.

Output: reports/forecast_latest.csv — P(alert) per region for each horizon.
"""
import sys
import warnings
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
warnings.filterwarnings("ignore", category=UserWarning)

import pandas as pd  # noqa: E402

from airalerts import config, evaluation as ev, models  # noqa: E402

if __name__ == "__main__":
    df = pd.read_parquet(config.DATA_PROCESSED / "dataset.parquet")
    last_origin = df.origin_time.max()
    train = df[ev.train_mask(df, df.origin_time.min(), last_origin)]
    model = models.LGBModel().fit(train)
    now = df[df.origin_time == last_origin].copy()
    now["p"] = model.predict(now)
    table = now.pivot(index="region", columns="h", values="p").round(3)
    table.columns = [f"+{h}h" for h in table.columns]
    table.insert(0, "alert_now", now[now.h == 1].set_index("region").active_now.astype(int))
    table = table.sort_values("+1h", ascending=False)
    table.to_csv(config.REPORTS / "forecast_latest.csv")
    local = last_origin.tz_convert(config.LOCAL_TZ)
    print(f"Forecast made at {local:%Y-%m-%d %H:%M} Kyiv time "
          f"(data up to that moment)\n")
    print(table.to_string())
