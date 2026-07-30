"""Constantes et helpers partages par tous les adaptateurs de donnees macro."""

import time

import requests

CURRENCIES = ["USD", "EUR", "GBP", "JPY", "AUD", "NZD", "CAD", "CHF"]

# Code pays/zone cote OECD (SDMX, norme ISO3 + codes zone euro)
OECD_AREA = {
    "USD": "USA",
    "EUR": "EA20",
    "GBP": "GBR",
    "JPY": "JPN",
    "AUD": "AUS",
    "NZD": "NZL",
    "CAD": "CAN",
    "CHF": "CHE",
}

# Certains dataflows OECD (PIB, chomage) ne couvrent pas l'agregat zone euro
# (EA20/EA19). L'Allemagne sert de proxy (premiere economie de la zone euro).
OECD_GDP_AREA_OVERRIDE = {"EUR": "DEU"}
OECD_UNEMPLOYMENT_AREA_OVERRIDE = {"EUR": "DEU"}

# Code pays cote BIS (taux directeurs), different de l'OECD pour la zone euro
BIS_AREA = {
    "USD": "US",
    "EUR": "XM",
    "GBP": "GB",
    "JPY": "JP",
    "AUD": "AU",
    "NZD": "NZ",
    "CAD": "CA",
    "CHF": "CH",
}

DEFAULT_TIMEOUT = 20
MAX_RETRIES = 4
BACKOFF_SECONDS = 10


def http_get(url: str, params: dict | None = None, headers: dict | None = None):
    """GET avec retry/backoff sur 429 (rate limit) - les APIs publiques gratuites
    (OECD, BIS) limitent le nombre de requetes par minute."""
    last_exc = None
    for attempt in range(MAX_RETRIES):
        resp = requests.get(url, params=params, headers=headers, timeout=DEFAULT_TIMEOUT)
        if resp.status_code == 429:
            last_exc = requests.HTTPError(f"429 Too Many Requests: {url}")
            time.sleep(BACKOFF_SECONDS * (attempt + 1))
            continue
        resp.raise_for_status()
        return resp
    raise last_exc
