"""
Lead Scorer — ranks KC metro zip codes by likelihood of insurance-covered roof replacements.

Composite score (0–100):
  40%  Damage probability  (from storm data)
  25%  Home value score    (sweet spot $180k–$450k = full score)
  20%  Owner-occupancy     (higher = more likely to file a claim)
  15%  Income score        (sweet spot $65k–$110k = full score)

Also tracks per-zip storm history: storms in last 3, 7, 14, 30 days
and the biggest hail ever recorded in the zip.
"""
import logging
from datetime import datetime, timedelta, timezone

from census_client import CensusClient

logger = logging.getLogger(__name__)

# Home value sweet spot for roofing leads:
# Too cheap = likely rental/mobile, too expensive = slow claim cycle
HOME_VALUE_MIN = 180_000
HOME_VALUE_MID = 300_000
HOME_VALUE_MAX = 450_000

# Income sweet spot: middle-class homeowners file claims fastest
INCOME_MIN = 50_000
INCOME_MID = 85_000
INCOME_MAX = 120_000


def _home_value_score(value: int) -> float:
    """Score home value 0–1. Peak at $180k–$450k range."""
    if value <= 0:
        return 0.5  # unknown = neutral
    if value < HOME_VALUE_MIN:
        return max(0.1, value / HOME_VALUE_MIN * 0.6)
    if value <= HOME_VALUE_MAX:
        return 1.0
    # Diminishing returns above sweet spot
    return max(0.4, 1.0 - (value - HOME_VALUE_MAX) / 500_000 * 0.6)


def _income_score(income: int) -> float:
    """Score income 0–1. Peak at $65k–$110k."""
    if income <= 0:
        return 0.5
    if income < INCOME_MIN:
        return max(0.2, income / INCOME_MIN * 0.5)
    if income <= INCOME_MAX:
        return 1.0
    # Very high income = still good but may self-pay; slight drop-off
    return max(0.5, 1.0 - (income - INCOME_MAX) / 200_000 * 0.5)


def _days_ago(dt_str: str, now: datetime) -> int | None:
    """Return how many days ago a storm_date string was. None if unparseable."""
    try:
        dt = datetime.fromisoformat(dt_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return (now - dt).days
    except (ValueError, TypeError):
        return None


async def score_leads(zones: list[dict]) -> list[dict]:
    """
    Given a list of damage zone dicts (from /api/zones), return a ranked list
    of zip codes with composite lead scores and storm history.

    Each entry in the returned list:
      zip             — zip code string
      score           — composite lead score 0–100
      max_hail        — biggest hail recorded in this zip (inches)
      damage_prob     — highest damage probability across hitting zones
      storms_3d       — # zones hitting this zip in last 3 days
      storms_7d       — # zones in last 7 days
      storms_14d      — # in last 14 days
      storms_30d      — # in last 30 days
      owner_rate      — fraction owner-occupied (0–1), or None
      median_income   — median household income ($), or None
      median_home_value — median home value ($), or None
      score_breakdown — dict with component scores
    """
    if not zones:
        return []

    now = datetime.now(timezone.utc)

    # ---- Build per-zip storm stats ----
    zip_stats: dict[str, dict] = {}

    for zone in zones:
        days_old = _days_ago(zone.get("storm_date", ""), now)
        prob = zone.get("damage_probability", 0.0)
        hail = zone.get("max_hail_inches", 0.0)

        for zc in zone.get("zip_codes", []):
            if zc not in zip_stats:
                zip_stats[zc] = {
                    "max_hail": 0.0,
                    "damage_prob": 0.0,
                    "storms_3d": 0,
                    "storms_7d": 0,
                    "storms_14d": 0,
                    "storms_30d": 0,
                }
            s = zip_stats[zc]
            s["max_hail"] = max(s["max_hail"], hail)
            s["damage_prob"] = max(s["damage_prob"], prob)
            if days_old is not None:
                if days_old <= 3:
                    s["storms_3d"] += 1
                if days_old <= 7:
                    s["storms_7d"] += 1
                if days_old <= 14:
                    s["storms_14d"] += 1
                if days_old <= 30:
                    s["storms_30d"] += 1

    if not zip_stats:
        return []

    # ---- Fetch Census demographics ----
    all_zips = list(zip_stats.keys())
    census = CensusClient()
    try:
        demo = await census.get_zip_demographics(all_zips)
    except Exception as e:
        logger.warning(f"Census fetch failed, scoring without demographics: {e}")
        demo = {}

    # ---- Compute composite scores ----
    results = []
    for zc, s in zip_stats.items():
        d = demo.get(zc, {})
        owner_rate = d.get("owner_rate")
        income = d.get("median_income")
        home_val = d.get("median_home_value")

        # Component scores (each 0–1)
        damage_score = s["damage_prob"]
        hv_score = _home_value_score(home_val or 0)
        owner_score = owner_rate if owner_rate is not None else 0.6
        inc_score = _income_score(income or 0)

        composite = (
            0.40 * damage_score
            + 0.25 * hv_score
            + 0.20 * owner_score
            + 0.15 * inc_score
        )

        results.append({
            "zip": zc,
            "score": round(composite * 100, 1),
            "max_hail": s["max_hail"],
            "damage_prob": round(s["damage_prob"] * 100, 1),
            "storms_3d": s["storms_3d"],
            "storms_7d": s["storms_7d"],
            "storms_14d": s["storms_14d"],
            "storms_30d": s["storms_30d"],
            "owner_rate": round(owner_rate * 100, 1) if owner_rate is not None else None,
            "median_income": income,
            "median_home_value": home_val,
            "score_breakdown": {
                "damage": round(damage_score * 100, 1),
                "home_value": round(hv_score * 100, 1),
                "owner_rate": round(owner_score * 100, 1),
                "income": round(inc_score * 100, 1),
            },
        })

    results.sort(key=lambda x: x["score"], reverse=True)
    logger.info(f"Lead scorer: ranked {len(results)} zip codes")
    return results
