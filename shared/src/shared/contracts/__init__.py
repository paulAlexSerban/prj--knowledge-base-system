"""Source data contracts. A connector starts only with a valid contract.

Call ``shared.contracts.validate.load_contracts`` at worker startup.
That import is not re-exported here so ``python -m shared.contracts.validate``
can run the module without a double-import warning.
"""

from shared.contracts.source_contract import (
    AclRule,
    Capability,
    FreshnessTier,
    Owner,
    PlatformAttribute,
    Sensitivity,
    SourceContract,
)

__all__ = [
    "AclRule",
    "Capability",
    "FreshnessTier",
    "Owner",
    "PlatformAttribute",
    "Sensitivity",
    "SourceContract",
]
