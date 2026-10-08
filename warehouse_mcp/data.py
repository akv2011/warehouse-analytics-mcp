"""A deterministic sample warehouse for a mobile app: installs, daily activity, purchases and ad revenue."""

from pathlib import Path

import duckdb

FIRST_DAY = "2026-07-01"
LAST_DAY = "2026-09-28"
TABLES = ("users", "sessions", "purchases", "ad_revenue")

BUILD = f"""
SELECT setseed(0.42);

CREATE TABLE users AS
SELECT i AS user_id,
       DATE '{FIRST_DAY}' + CAST(floor(random() * 90) AS INTEGER) AS install_date,
       (['US', 'IN', 'GB', 'DE', 'BR', 'JP', 'CA', 'FR'])[1 + CAST(floor(random() * 8) AS INTEGER)] AS country,
       CASE WHEN random() < 0.55 THEN 'android' ELSE 'ios' END AS platform,
       (['organic', 'paid_social', 'search', 'video_ads', 'referral'])[1 + CAST(floor(random() * 5) AS INTEGER)] AS channel
FROM range(1, 20001) t(i);

CREATE TABLE sessions AS
SELECT u.user_id,
       u.install_date + CAST(d.day AS INTEGER) AS session_date,
       1 + CAST(floor(random() * 4) AS INTEGER) AS sessions,
       round(2 + random() * 30, 1) AS minutes
FROM users u, range(0, 90) d(day)
WHERE u.install_date + CAST(d.day AS INTEGER) <= DATE '{LAST_DAY}'
  -- The draw must depend on both user and day: random() here would be pushed below the join and
  -- shared by every user on the same day offset.
  AND (hash(u.user_id, d.day) % 1000000) / 1000000.0 < CASE WHEN d.day = 0 THEN 1.0 ELSE 0.45 * pow(d.day, -0.35) END;

CREATE TABLE purchases AS
SELECT row_number() OVER (ORDER BY s.user_id, s.session_date) AS purchase_id,
       s.user_id,
       CAST(s.session_date AS TIMESTAMP) + to_seconds(CAST(floor(random() * 86400) AS BIGINT)) AS purchased_at,
       CAST(([0.99, 4.99, 9.99, 19.99, 49.99])[1 + CAST(floor(random() * 5) AS INTEGER)] AS DECIMAL(10, 2)) AS amount_usd,
       (['coins_small', 'coins_large', 'no_ads', 'starter_pack', 'season_pass'])[1 + CAST(floor(random() * 5) AS INTEGER)] AS sku
FROM sessions s
WHERE random() < 0.03;

CREATE TABLE ad_revenue AS
SELECT s.user_id,
       s.session_date AS revenue_date,
       (['network_a', 'network_b', 'network_c'])[1 + CAST(floor(random() * 3) AS INTEGER)] AS network,
       round(s.sessions * (0.002 + random() * 0.02), 4) AS revenue_usd
FROM sessions s
WHERE random() < 0.6;
"""


def build(path: Path) -> Path:
    """Write the sample warehouse to `path`; one thread keeps the seeded random() repeatable."""
    path.unlink(missing_ok=True)
    con = duckdb.connect(str(path), config={"threads": 1})
    try:
        con.execute(BUILD)
    finally:
        con.close()
    return path
