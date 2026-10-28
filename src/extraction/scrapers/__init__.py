"""
Per-company custom scrapers.

Each ticker gets a module in this package with a scraper class deriving from
`BaseCompanyScraper`.  Register new scrapers in `SCRAPERS` so the unified
runner (`scripts/run_company_scraper.py`) can find them.

`PORTING_QUEUE` is the working order for migrating the remaining BDCs onto
this framework (roughly largest / most important first).  Move a ticker from
the queue into `SCRAPERS` once its scraper is implemented and tested.
"""

from __future__ import annotations

from typing import Dict, Type

from .base import BaseCompanyScraper
from .arcc import ARCCScraper
from .fsk import FSKScraper
from .obdc import OBDCScraper
from .bxsl import BXSLScraper
from .main import MAINScraper
from .gbdc import GBDCScraper
from .bbdc import BBDCScraper
from .ocsl import OCSLScraper
from .htgc import HTGCScraper
from .cgbd import CGBDScraper
from .ccap import CCAPScraper
from .gsbd import GSBDScraper
from .bcsf import BCSFScraper
from .tslx import TSLXScraper
from .nmfc import NMFCScraper
from .cswc import CSWCScraper
from .psec import PSECScraper
from .slrc import SLRCScraper
from .pnnt import PNNTScraper
from .pflt import PFLTScraper
from .tcpc import TCPCScraper
from .fdus import FDUSScraper
from .cion import CIONScraper
from .bcic import BCICScraper
from .glad import GLADScraper
from .gain import GAINScraper
from .trin import TRINScraper
from .hrzn import HRZNScraper
from .sar import SARScraper

SCRAPERS: Dict[str, Type[BaseCompanyScraper]] = {
    "ARCC": ARCCScraper,
    "FSK": FSKScraper,
    "OBDC": OBDCScraper,
    "BXSL": BXSLScraper,
    "MAIN": MAINScraper,
    "GBDC": GBDCScraper,
    "BBDC": BBDCScraper,
    "OCSL": OCSLScraper,
    "HTGC": HTGCScraper,
    "CGBD": CGBDScraper,
    "CCAP": CCAPScraper,
    "GSBD": GSBDScraper,
    "BCSF": BCSFScraper,
    "TSLX": TSLXScraper,
    "NMFC": NMFCScraper,
    "CSWC": CSWCScraper,
    "PSEC": PSECScraper,
    "SLRC": SLRCScraper,
    "PNNT": PNNTScraper,
    "PFLT": PFLTScraper,
    "TCPC": TCPCScraper,
    "FDUS": FDUSScraper,
    "CION": CIONScraper,
    "BCIC": BCICScraper,
    "GLAD": GLADScraper,
    "GAIN": GAINScraper,
    "TRIN": TRINScraper,
    "HRZN": HRZNScraper,
    "SAR": SARScraper,
}

# Remaining BDCs to port, in priority order.
PORTING_QUEUE = ["MRCC", "SCM", "WHF", "MFIC", "MSDL",
    "KBDC", "LIEN", "NCDL", "MSIF", "OFS", "OXSQ", "GECC", "ICMB",
    "PFX", "PSBD", "RAND", "RWAY", "SSSS", "EQS", "TPVG", "BCRED",
]


def get_scraper(ticker: str, **kwargs) -> BaseCompanyScraper:
    """Return a scraper instance for `ticker` (case-insensitive)."""
    key = ticker.upper()
    if key not in SCRAPERS:
        raise KeyError(
            f"No custom scraper registered for {ticker}. "
            f"Registered: {sorted(SCRAPERS)}"
        )
    return SCRAPERS[key](**kwargs)
