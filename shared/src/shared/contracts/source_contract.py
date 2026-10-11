"""Pydantic model for a per-source data contract.

The model is the source of truth. JSON Schema is generated from it.
A connector must not be registered or started unless ``load_contracts``
accepts its YAML file. Required gates: owner, freshness tier, and at
least one ACL mapping rule.
"""

from enum import Enum

from pydantic import BaseModel, ConfigDict, EmailStr, Field, model_validator

FORBIDDEN_IN_SCOPE_FIELDS = frozenset(
    {"salary", "compensation", "ssn", "national_id"}
)
PUSH_OR_CURSOR = frozenset({"webhook", "cursor_feed", "updated_since"})
TIER_MAX_SECONDS = {"P0": 30, "P1": 300, "P2": 86400}
CLEARANCE_LABELS = ("public", "internal", "confidential", "restricted")
REQUIRED_ACL_ATTRIBUTES = ("group", "clearance", "department")


class FreshnessTier(str, Enum):
    P0 = "P0"
    P1 = "P1"
    P2 = "P2"


class Sensitivity(str, Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class Capability(str, Enum):
    WEBHOOK = "webhook"
    CURSOR_FEED = "cursor_feed"
    UPDATED_SINCE = "updated_since"
    SNAPSHOT_DIFF = "snapshot_diff"


class PlatformAttribute(str, Enum):
    DEPARTMENT = "department"
    CLEARANCE = "clearance"
    REGION = "region"
    GROUP = "group"


class Owner(BaseModel):
    model_config = ConfigDict(extra="forbid")

    team: str = Field(min_length=1)
    contact: EmailStr


class AclRule(BaseModel):
    """One native permission mapped onto group, clearance, and department.

    All three are required. ``department`` may be ``*``, which means every
    department. A missing attribute does not grant access. ``region`` stays
    optional and is not an access predicate.
    """

    model_config = ConfigDict(extra="forbid")

    native: str = Field(min_length=1)
    attributes: dict[PlatformAttribute, str] = Field(
        min_length=1,
        description=(
            "Requires group, clearance, and department. "
            "clearance is public, internal, confidential, or restricted. "
            "department * means every department."
        ),
    )

    @model_validator(mode="after")
    def require_explicit_grant(self) -> "AclRule":
        values = {key.value: value for key, value in self.attributes.items()}
        missing = [
            name
            for name in REQUIRED_ACL_ATTRIBUTES
            if not isinstance(values.get(name), str) or not values[name].strip()
        ]
        if missing:
            raise ValueError(
                "acl rule requires group, clearance, and department: " + ", ".join(missing)
            )
        clearance = values["clearance"].strip()
        if clearance not in CLEARANCE_LABELS:
            raise ValueError("clearance must be one of " + ", ".join(CLEARANCE_LABELS))
        return self


class SourceContract(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_id: str = Field(pattern=r"^[a-z][a-z0-9_]{1,63}$")
    display_name: str = Field(min_length=1)
    owner: Owner
    freshness_tier: FreshnessTier
    freshness_target_seconds: int = Field(gt=0)
    sensitivity: Sensitivity
    capabilities: list[Capability] = Field(min_length=1)
    acl_mapping: list[AclRule] = Field(min_length=1)
    in_scope_fields: list[str] = Field(min_length=1)
    excluded_fields: list[str] = Field(default_factory=list)
    acl_recheck_at_read: bool

    @model_validator(mode="after")
    def check_cross_field_rules(self) -> "SourceContract":
        capabilities = [item.value for item in self.capabilities]
        if len(capabilities) != len(set(capabilities)):
            raise ValueError("capabilities must be unique")

        if self.freshness_tier is FreshnessTier.P0 and not (
            set(capabilities) & PUSH_OR_CURSOR
        ):
            raise ValueError(
                "P0 requires webhook, cursor_feed, or updated_since; "
                "snapshot_diff alone cannot meet the tier"
            )

        tier_limit = TIER_MAX_SECONDS[self.freshness_tier.value]
        if self.freshness_target_seconds > tier_limit:
            raise ValueError(
                f"freshness_target_seconds {self.freshness_target_seconds} "
                f"exceeds {tier_limit} for tier {self.freshness_tier.value}"
            )

        if self.sensitivity is Sensitivity.HIGH and not self.acl_recheck_at_read:
            raise ValueError(
                "sensitivity high requires acl_recheck_at_read true"
            )

        in_scope = [field.casefold() for field in self.in_scope_fields]
        excluded = {field.casefold() for field in self.excluded_fields}
        if len(in_scope) != len(set(in_scope)):
            raise ValueError("in_scope_fields must be unique")
        overlap = sorted(set(in_scope) & excluded)
        if overlap:
            raise ValueError(
                "fields cannot be both in_scope_fields and excluded_fields: "
                + ", ".join(overlap)
            )
        forbidden = sorted(set(in_scope) & FORBIDDEN_IN_SCOPE_FIELDS)
        if forbidden:
            raise ValueError(
                "in_scope_fields rejects data-minimization fields: "
                + ", ".join(forbidden)
            )
        return self
