CONDITIONS = ("A_no_rag", "B_naive_rag", "C_verified_rag")

def apply_context_policy(condition, records):
    if condition == "A_no_rag":
        return []
    if condition == "B_naive_rag":
        return list(records)
    if condition == "C_verified_rag":
        return [r for r in records if r["verification"]["status"] == "verified"]
    raise ValueError(f"Unknown condition: {condition}")
