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


def target_eq_to_cutoff(eq: str) -> float:
    mapping = {
        "1.00": 99.0,
        "1.25": 96.0,
        "1.50": 93.0,
        "1.75": 90.0,
        "2.00": 87.0,
        "2.25": 84.0,
        "2.50": 81.0,
        "2.75": 78.0,
        "3.00": 75.0,
    }
    return mapping.get(eq, 75.0)


def normalize_mode(mode: str) -> str:
    cleaned = mode.strip().casefold().replace("-", "").replace("/", "")
    if cleaned in {"prelim", "prelims"}:
        return "prelim"
    if cleaned in {"term", "midterm", "midterms", "midtermsfinals", "finals"}:
        return "term"
    raise ValueError("Mode must be `prelim` or `term`.")


def compute_final_grade(
    *,
    mode: str,
    class_standing: float,
    exam_percent: float,
    previous_term_average: float = 0.0,
) -> float:
    normalized_mode = normalize_mode(mode)
    if normalized_mode == "prelim":
        return class_standing * 0.5 + exam_percent * 0.5
    return (class_standing + exam_percent + previous_term_average) / 3


def required_exam_percentage(
    *,
    mode: str,
    target_equivalence: str,
    class_standing: float,
    previous_term_average: float = 0.0,
) -> float:
    normalized_mode = normalize_mode(mode)
    target_percent = target_eq_to_cutoff(target_equivalence)
    if normalized_mode == "prelim":
        return (target_percent - class_standing * 0.5) / 0.5
    return (target_percent - previous_term_average / 3 - class_standing / 3) / (1 / 3)
