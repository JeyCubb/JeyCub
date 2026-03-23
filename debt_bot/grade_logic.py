from __future__ import annotations


def calculate_percentage(score: float, total: float) -> float:
    if total <= 0:
        raise ValueError("Total must be greater than 0.")
    return (score / total) * 100


def percent_to_equivalence(percent: float) -> str:
    if percent >= 99:
        return "1.00"
    if percent >= 96:
        return "1.25"
    if percent >= 93:
        return "1.50"
    if percent >= 90:
        return "1.75"
    if percent >= 87:
        return "2.00"
    if percent >= 84:
        return "2.25"
    if percent >= 81:
        return "2.50"
    if percent >= 78:
        return "2.75"
    if percent >= 75:
        return "3.00"
    return "4.00"

