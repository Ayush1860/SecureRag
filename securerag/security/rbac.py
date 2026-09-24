
from typing import TypedDict


class RolePolicy(TypedDict):
    max_clearance: str
    departments: list[str]


CLEARANCE_LEVELS: dict[str, int] = {"public": 0, "internal": 1, "confidential": 2}

ROLE_POLICY: dict[str, RolePolicy] = {
    "guest": {"max_clearance": "public", "departments": ["general"]},
    "employee": {"max_clearance": "internal", "departments": ["general", "engineering", "hr"]},
    "finance_lead": {"max_clearance": "confidential", "departments": ["general", "finance"]},
    "exec": {"max_clearance": "confidential", "departments": ["general", "engineering", "hr", "finance", "exec"]},
    # Operators: may read the audit log and run admin jobs, but see no documents (separation of duties).
    "admin": {"max_clearance": "public", "departments": []},
}

class AccessControlError(ValueError):
    pass


def validate_role(role: str) -> None:
    if role not in ROLE_POLICY:
        raise AccessControlError(f"Unknown role: {role}")


def build_chroma_filter(user_role: str) -> dict:
    validate_role(user_role)
    policy = ROLE_POLICY[user_role]
    max_level = CLEARANCE_LEVELS[policy["max_clearance"]]
    allowed = [c for c, level in CLEARANCE_LEVELS.items() if level <= max_level]
    return {"$and": [{"clearance": {"$in": allowed}}, {"department": {"$in": policy["departments"]}}]}


def authorize(user_role: str, metadata: dict) -> bool:
    if user_role not in ROLE_POLICY:
        return False
    policy = ROLE_POLICY[user_role]
    max_level = CLEARANCE_LEVELS[policy["max_clearance"]]
    chunk_level = CLEARANCE_LEVELS.get(metadata.get("clearance", "confidential"), 99)
    return chunk_level <= max_level and metadata.get("department") in policy["departments"]


# Every department referenced by some role. Ingestion rejects documents labelled with anything else.
DEPARTMENTS: frozenset[str] = frozenset(d for policy in ROLE_POLICY.values() for d in policy["departments"])


# ---------------------------------------------------------------------------------------
# Partitions: every chunk lives in exactly one (department, clearance) partition. Dense and
# sparse indexes are physically split by partition, so a role's query only ever touches the
# partitions its policy allows.
# ---------------------------------------------------------------------------------------

def partition_name(department: str, clearance: str) -> str:
    if department not in DEPARTMENTS or clearance not in CLEARANCE_LEVELS:
        raise ValueError(f"invalid partition labels: {department!r}/{clearance!r}")
    return f"{department}.{clearance}"


def all_partitions() -> list[str]:
    return [partition_name(d, c) for d in sorted(DEPARTMENTS) for c in CLEARANCE_LEVELS]


def partitions_for_filter(where: dict | None) -> list[str]:
    """Partitions matching a Chroma-style RBAC filter ({"$and": [{"department": {"$in": ...}}, ...]}).

    Fails closed: a filter this function does not understand yields no partitions.
    """
    if not where:
        return all_partitions()
    departments: set[str] | None = None
    clearances: set[str] | None = None
    for cond in where.get("$and", [where]):
        if not isinstance(cond, dict):
            return []
        for field, value in cond.items():
            if isinstance(value, dict) and set(value) == {"$in"}:
                allowed = set(value["$in"])
            elif isinstance(value, str):
                allowed = {value}
            else:
                return []
            if field == "department":
                departments = allowed if departments is None else departments & allowed
            elif field == "clearance":
                clearances = allowed if clearances is None else clearances & allowed
            else:
                return []
    departments = (departments if departments is not None else set(DEPARTMENTS)) & set(DEPARTMENTS)
    clearances = (clearances if clearances is not None else set(CLEARANCE_LEVELS)) & set(CLEARANCE_LEVELS)
    return [partition_name(d, c) for d in sorted(departments) for c in sorted(clearances)]


def partitions_for_role(user_role: str) -> list[str]:
    return partitions_for_filter(build_chroma_filter(user_role))


def metadata_matches_filter(metadata: dict, where: dict | None) -> bool:
    """Evaluate a Chroma-style RBAC filter against chunk metadata. Unknown operators fail closed."""
    if not where:
        return True
    for cond in where.get("$and", [where]):
        if not isinstance(cond, dict):
            return False
        for key, value in cond.items():
            actual = metadata.get(key)
            if isinstance(value, dict):
                if set(value) == {"$in"}:
                    if actual not in value["$in"]:
                        return False
                elif set(value) == {"$eq"}:
                    if actual != value["$eq"]:
                        return False
                else:
                    return False
            elif actual != value:
                return False
    return True
