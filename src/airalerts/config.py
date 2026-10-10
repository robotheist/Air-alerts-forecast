"""Project-wide constants: paths, regions, adjacency graph, forecast setup."""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATA_RAW = ROOT / "data" / "raw"
DATA_PROCESSED = ROOT / "data" / "processed"
REPORTS = ROOT / "reports"
FIGURES = REPORTS / "figures"
METRICS = REPORTS / "metrics"

DATASET_URL = (
    "https://raw.githubusercontent.com/Vadimkin/ukrainian-air-raid-sirens-dataset/"
    "main/datasets/volunteer_data_en.csv"
)
RAW_CSV = DATA_RAW / "volunteer_data_en.csv"

# Time zone used for calendar features (alerts are stored in UTC).
LOCAL_TZ = "Europe/Kyiv"

# Forecast horizons (hours ahead of the forecast origin).
HORIZONS = (1, 3, 6, 12, 24)

# Evaluation: the last TEST_DAYS days of available data form the hold-out test set,
# the VAL_DAYS days before that are used for model selection.
TEST_DAYS = 28
VAL_DAYS = 28

# Luhansk oblast has had a permanent alert since April 2022 and is excluded.
# Kyiv City is treated as a separate region (as in the dataset).
NEIGHBOURS: dict[str, list[str]] = {
    "Volynska oblast": ["Rivnenska oblast", "Lvivska oblast"],
    "Rivnenska oblast": ["Volynska oblast", "Lvivska oblast", "Ternopilska oblast",
                         "Khmelnytska oblast", "Zhytomyrska oblast"],
    "Lvivska oblast": ["Volynska oblast", "Rivnenska oblast", "Ternopilska oblast",
                       "Ivano-Frankivska oblast", "Zakarpatska oblast"],
    "Zakarpatska oblast": ["Lvivska oblast", "Ivano-Frankivska oblast"],
    "Ivano-Frankivska oblast": ["Lvivska oblast", "Zakarpatska oblast",
                                "Chernivetska oblast", "Ternopilska oblast"],
    "Ternopilska oblast": ["Lvivska oblast", "Rivnenska oblast", "Khmelnytska oblast",
                           "Chernivetska oblast", "Ivano-Frankivska oblast"],
    "Chernivetska oblast": ["Ivano-Frankivska oblast", "Ternopilska oblast",
                            "Khmelnytska oblast", "Vinnytska oblast"],
    "Khmelnytska oblast": ["Rivnenska oblast", "Ternopilska oblast", "Chernivetska oblast",
                           "Vinnytska oblast", "Zhytomyrska oblast"],
    "Zhytomyrska oblast": ["Rivnenska oblast", "Khmelnytska oblast", "Vinnytska oblast",
                           "Kyivska oblast"],
    "Vinnytska oblast": ["Zhytomyrska oblast", "Khmelnytska oblast", "Chernivetska oblast",
                         "Odeska oblast", "Kirovohradska oblast", "Cherkaska oblast",
                         "Kyivska oblast"],
    "Kyivska oblast": ["Zhytomyrska oblast", "Vinnytska oblast", "Cherkaska oblast",
                       "Poltavska oblast", "Chernihivska oblast", "Kyiv City"],
    "Kyiv City": ["Kyivska oblast"],
    "Chernihivska oblast": ["Kyivska oblast", "Sumska oblast", "Poltavska oblast"],
    "Sumska oblast": ["Chernihivska oblast", "Poltavska oblast", "Kharkivska oblast"],
    "Poltavska oblast": ["Kyivska oblast", "Chernihivska oblast", "Sumska oblast",
                         "Kharkivska oblast", "Dnipropetrovska oblast",
                         "Kirovohradska oblast", "Cherkaska oblast"],
    "Cherkaska oblast": ["Kyivska oblast", "Vinnytska oblast", "Kirovohradska oblast",
                         "Poltavska oblast"],
    "Kirovohradska oblast": ["Cherkaska oblast", "Vinnytska oblast", "Odeska oblast",
                             "Mykolaivska oblast", "Dnipropetrovska oblast",
                             "Poltavska oblast"],
    "Odeska oblast": ["Vinnytska oblast", "Kirovohradska oblast", "Mykolaivska oblast"],
    "Mykolaivska oblast": ["Odeska oblast", "Kirovohradska oblast",
                           "Dnipropetrovska oblast", "Khersonska oblast"],
    "Khersonska oblast": ["Mykolaivska oblast", "Dnipropetrovska oblast", "Zaporizka oblast"],
    "Dnipropetrovska oblast": ["Poltavska oblast", "Kharkivska oblast", "Donetska oblast",
                               "Zaporizka oblast", "Khersonska oblast",
                               "Mykolaivska oblast", "Kirovohradska oblast"],
    "Zaporizka oblast": ["Dnipropetrovska oblast", "Donetska oblast", "Khersonska oblast"],
    "Donetska oblast": ["Kharkivska oblast", "Dnipropetrovska oblast", "Zaporizka oblast"],
    "Kharkivska oblast": ["Sumska oblast", "Poltavska oblast", "Dnipropetrovska oblast",
                          "Donetska oblast"],
}
REGIONS: list[str] = sorted(NEIGHBOURS)
