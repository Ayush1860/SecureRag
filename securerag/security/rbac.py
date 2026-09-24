from dataclasses import dataclass

CLEARANCE_LEVELS = {"public": 0, "internal": 1, "confidential": 2}

ROLE_POLICY = {
    "guest": {"max_clearance": "public", "departments": ["general"]},
    "employee": {"max_clearance": "internal", "departments": ["general", "engineering", "hr"]},
    "finance_lead": {"max_clearance": "confidential", "departments": ["general", "finance"]},
    "exec": {"max_clearance": "confidential", "departments": ["general", "engineering", "hr", "finance", "exec"]},
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
