from __future__ import annotations

"""
Backward-compatible re-exports for legacy callers.

New code should use `core/contracts_v2.py`:
- `normalize_and_validate(contract_name, raw_obj, context)`
- `CONTRACT_SUMMARY`
"""

from core.contracts_v2 import CONTRACT_SUMMARY, normalize_and_validate

__all__ = ["normalize_and_validate", "CONTRACT_SUMMARY"]
