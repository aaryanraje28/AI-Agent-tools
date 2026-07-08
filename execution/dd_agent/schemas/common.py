"""Shared enums and value types used across DD agent schemas."""
from __future__ import annotations

from enum import Enum


class DDCategory(str, Enum):
    CORPORATE_LEGAL = "Corporate/Legal"
    FINANCIAL = "Financial"
    TAX = "Tax"
    COMMERCIAL_OPERATIONS = "Commercial/Operations"
    HR = "HR"
    IP = "IP"
    LITIGATION = "Litigation"
    COMPLIANCE_REGULATORY = "Compliance/Regulatory"
    REAL_ESTATE_ASSETS = "Real Estate/Assets"
    UNCLASSIFIED = "Unclassified"


class Severity(str, Enum):
    HIGH = "High"
    MEDIUM = "Medium"
    LOW = "Low"


class Confidence(str, Enum):
    HIGH = "High"
    MEDIUM = "Medium"
    LOW = "Low"


class DocumentStatus(str, Enum):
    OK = "ok"
    LOCKED = "locked"
    UNREADABLE = "unreadable"
    OCR_LOW_CONFIDENCE = "ocr_low_confidence"
