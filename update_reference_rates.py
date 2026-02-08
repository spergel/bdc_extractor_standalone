#!/usr/bin/env python3
"""
Fetch current reference rates from public APIs and update the frontend.

This script fetches:
- SOFR (Secured Overnight Financing Rate) from NY Fed
- Prime Rate from FRED API (Federal Reserve Economic Data)
- Treasury rates from Treasury.gov

Run this daily to keep rates current.
"""

import json
import logging
import requests
from pathlib import Path
from datetime import datetime, timedelta
from typing import Dict, Optional

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


def fetch_sofr_rate() -> Optional[float]:
    """
    Fetch current SOFR rate from NY Fed API.
    https://markets.newyorkfed.org/read?productCode=50&eventCodes=500&limit=1&startPosition=0&sort=postDt:-1&format=json
    """
    try:
        url = "https://markets.newyorkfed.org/api/rates/secured/sofr/last/1.json"
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        
        data = response.json()
        if data and 'refRates' in data and len(data['refRates']) > 0:
            rate = float(data['refRates'][0]['percentRate'])
            logger.info(f"✓ Fetched SOFR: {rate}%")
            return rate
        
        logger.warning("SOFR data not found in response")
        return None
    except Exception as e:
        logger.error(f"Failed to fetch SOFR: {e}")
        return None


def fetch_fred_rate(series_id: str, rate_name: str) -> Optional[float]:
    """
    Fetch rate from FRED API (Federal Reserve Economic Data).
    Note: FRED API is free but rate-limited without API key.
    
    Common series IDs:
    - DPRIME: Bank Prime Loan Rate
    - DGS10: 10-Year Treasury Constant Maturity Rate
    - DGS5: 5-Year Treasury
    - DGS2: 2-Year Treasury
    """
    try:
        # Using the public JSON API (limited to recent data)
        url = f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={series_id}"
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        
        # Parse CSV (last line has most recent data)
        lines = response.text.strip().split('\n')
        if len(lines) < 2:
            logger.warning(f"No data for {rate_name}")
            return None
        
        # Get last non-empty data point
        for line in reversed(lines[1:]):  # Skip header
            parts = line.split(',')
            if len(parts) >= 2 and parts[1] and parts[1] != '.':
                rate = float(parts[1])
                logger.info(f"✓ Fetched {rate_name}: {rate}%")
                return rate
        
        logger.warning(f"No valid data found for {rate_name}")
        return None
    except Exception as e:
        logger.error(f"Failed to fetch {rate_name}: {e}")
        return None


def fetch_treasury_rates() -> Dict[str, Optional[float]]:
    """
    Fetch Treasury rates from Treasury.gov API.
    """
    rates = {
        '2Y': None,
        '5Y': None,
        '10Y': None,
    }
    
    try:
        # Treasury.gov has a JSON API for daily rates
        url = "https://home.treasury.gov/resource-center/data-chart-center/interest-rates/daily-treasury-rates.csv/all/latest"
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        
        # Parse CSV
        lines = response.text.strip().split('\n')
        if len(lines) < 2:
            return rates
        
        # Get header and find column indices
        header = lines[0].split(',')
        
        # Map column names to our rate keys
        column_map = {
            '2 Yr': '2Y',
            '5 Yr': '5Y',
            '10 Yr': '10Y',
        }
        
        # Get latest data (last line)
        data = lines[-1].split(',')
        
        for col_name, rate_key in column_map.items():
            try:
                col_idx = header.index(col_name)
                if col_idx < len(data) and data[col_idx]:
                    rate = float(data[col_idx])
                    rates[rate_key] = rate
                    logger.info(f"✓ Fetched Treasury {rate_key}: {rate}%")
            except (ValueError, IndexError):
                pass
        
    except Exception as e:
        logger.error(f"Failed to fetch Treasury rates: {e}")
    
    return rates


def update_reference_rates():
    """
    Fetch all reference rates and update the frontend JSON file.
    """
    logger.info("=== Updating Reference Rates ===")
    
    # Fetch rates from various sources
    sofr = fetch_sofr_rate()
    prime = fetch_fred_rate('DPRIME', 'Prime Rate')
    treasury_rates = fetch_treasury_rates()
    
    # Build rates dictionary
    rates = {
        'last_updated': datetime.now().isoformat(),
        'rates': {}
    }
    
    # SOFR rates (use same rate for all SOFR variants)
    if sofr:
        rates['rates']['SOFR'] = sofr
        rates['rates']['TERM SOFR'] = sofr
        rates['rates']['1M SOFR'] = sofr
        rates['rates']['3M SOFR'] = sofr
        rates['rates']['6M SOFR'] = sofr
    
    # Prime rate
    if prime:
        rates['rates']['PRIME'] = prime
        rates['rates']['P'] = prime
        rates['rates']['BASE RATE'] = prime
    
    # LIBOR (legacy, being phased out - use estimated value)
    # Since LIBOR is discontinued, estimate based on SOFR + spread
    if sofr:
        libor_estimate = sofr + 0.26  # Typical SOFR-LIBOR spread
        rates['rates']['LIBOR'] = libor_estimate
        rates['rates']['L'] = libor_estimate
        rates['rates']['1M LIBOR'] = libor_estimate
        rates['rates']['3M LIBOR'] = libor_estimate
        rates['rates']['6M LIBOR'] = libor_estimate
    
    # Treasury rates
    if treasury_rates.get('10Y'):
        rates['rates']['10Y UST'] = treasury_rates['10Y']
    if treasury_rates.get('5Y'):
        rates['rates']['5Y UST'] = treasury_rates['5Y']
    if treasury_rates.get('2Y'):
        rates['rates']['2Y UST'] = treasury_rates['2Y']
    
    # Fixed rate (always 0)
    rates['rates']['FIXED'] = 0
    
    # Write to JSON file
    output_path = Path('frontend/public/data/reference_rates.json')
    output_path.parent.mkdir(parents=True, exist_ok=True)
    
    with open(output_path, 'w') as f:
        json.dump(rates, f, indent=2)
    
    logger.info(f"✓ Updated {len(rates['rates'])} reference rates")
    logger.info(f"✓ Saved to {output_path}")
    
    # Print summary
    print("\n=== Current Reference Rates ===")
    for key, value in sorted(rates['rates'].items()):
        if value is not None:
            print(f"  {key:20s}: {value:6.2f}%")
    print(f"\nLast updated: {rates['last_updated']}")
    
    return rates


if __name__ == '__main__':
    update_reference_rates()
