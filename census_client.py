"""
US Census Bureau ACS (American Community Survey) API client.

Fetches demographic data for KC metro zip codes used to score
lead quality — owner-occupancy rate, median income, median home value,
median year structure built, and share of pre-1980 housing stock.

No API key required for up to 500 requests/day.
API docs: https://api.census.gov/data/2024/acs/acs5/variables.html
"""
import logging
import httpx

logger = logging.getLogger(__name__)

# ACS 5-year estimates (2024 — released December 2025, most recent available)
CENSUS_URL = "https://api.census.gov/data/2024/acs/acs5"

# Variables:
#   B25003_001E  Total occupied housing units
#   B25003_002E  Owner-occupied units
#   B19013_001E  Median household income
#   B25077_001E  Median home value (owner-occupied)
#   B25035_001E  Median year structure built
#   B25034_001E  Total housing units (year-built breakdown denominator)
#   B25034_007E  Built 1970–1979
#   B25034_008E  Built 1960–1969
#   B25034_009E  Built 1950–1959
#   B25034_010E  Built 1940–1949
#   B25034_011E  Built 1939 or earlier
VARIABLES = (
    "B25003_001E,B25003_002E,B19013_001E,B25077_001E,"
    "B25035_001E,B25034_001E,B25034_007E,B25034_008E,"
    "B25034_009E,B25034_010E,B25034_011E"
)

# Max zip codes per request — Census API handles ~50 at a time reliably
BATCH_SIZE = 50


class CensusClient:
    """US Census Bureau ACS API client."""

    HEADERS = {"User-Agent": "StormLeads/1.0 (contact@stormleads.com)"}

    async def get_zip_demographics(
        self, zip_codes: list[str]
    ) -> dict[str, dict]:
        """
        Fetch ACS demographics for a list of zip codes.

        Returns dict keyed by zip code:
          owner_rate        — fraction of units owner-occupied (0.0–1.0)
          median_income     — median household income ($)
          median_home_value — median home value ($)
          total_units       — total occupied housing units
          median_year_built — median year structure was built
          pct_pre1980       — fraction of housing stock built before 1980 (0.0–1.0)
        """
        if not zip_codes:
            return {}

        results = {}
        unique_zips = list(dict.fromkeys(zip_codes))  # deduplicate, preserve order

        # Batch requests to stay within Census API limits
        for i in range(0, len(unique_zips), BATCH_SIZE):
            batch = unique_zips[i : i + BATCH_SIZE]
            batch_results = await self._fetch_batch(batch)
            results.update(batch_results)

        logger.info(
            f"Census ACS: got demographics for {len(results)}/{len(unique_zips)} zip codes"
        )
        return results

    async def _fetch_batch(self, zip_codes: list[str]) -> dict[str, dict]:
        """Fetch one batch of zip codes from the Census API."""
        params = {
            "get": VARIABLES,
            "for": f"zip code tabulation area:{','.join(zip_codes)}",
        }
        try:
            async with httpx.AsyncClient(
                headers=self.HEADERS, timeout=30.0
            ) as client:
                resp = await client.get(CENSUS_URL, params=params)
                resp.raise_for_status()
                data = resp.json()
        except httpx.HTTPError as e:
            logger.warning(f"Census API batch request failed: {e}")
            return {}

        if not data or len(data) < 2:
            return {}

        headers = data[0]
        results = {}

        for row in data[1:]:
            r = dict(zip(headers, row))
            zcta = r.get("zip code tabulation area", "")
            if not zcta:
                continue
            try:
                total = int(r.get("B25003_001E") or 0)
                owned = int(r.get("B25003_002E") or 0)
                income = int(r.get("B19013_001E") or 0)
                home_val = int(r.get("B25077_001E") or 0)
                median_yr = int(r.get("B25035_001E") or 0)
                yr_total = int(r.get("B25034_001E") or 0)
                pre1980 = sum([
                    int(r.get("B25034_007E") or 0),  # 1970-1979
                    int(r.get("B25034_008E") or 0),  # 1960-1969
                    int(r.get("B25034_009E") or 0),  # 1950-1959
                    int(r.get("B25034_010E") or 0),  # 1940-1949
                    int(r.get("B25034_011E") or 0),  # 1939 or earlier
                ])

                results[zcta] = {
                    "owner_rate": round(owned / total, 3) if total > 0 else 0.6,
                    "median_income": max(income, 0),
                    "median_home_value": max(home_val, 0),
                    "total_units": total,
                    "median_year_built": max(median_yr, 0),
                    "pct_pre1980": round(pre1980 / yr_total, 3) if yr_total > 0 else 0.0,
                }
            except (ValueError, TypeError):
                pass

        return results
