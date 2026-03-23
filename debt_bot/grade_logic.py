from __future__ import annotations

from dataclasses import dataclass


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


@dataclass(frozen=True)
class GradeCategory:
    weight: float
    items: list[tuple[float, float]]


def parse_categories_input(raw_value: str) -> list[GradeCategory]:
    cleaned = raw_value.strip()
    if not cleaned:
        raise ValueError("Categories are required.")

    categories: list[GradeCategory] = []
    for raw_category in cleaned.split(";"):
        category_text = raw_category.strip()
        if not category_text:
            continue

        if "|" not in category_text:
            raise ValueError("Each category must look like `30|18/20,19/20`.")

        raw_weight, raw_items = category_text.split("|", 1)
        try:
            weight = float(raw_weight.strip())
        except ValueError as exc:
            raise ValueError("Category weight must be a number.") from exc

        items: list[tuple[float, float]] = []
        for raw_item in raw_items.split(","):
            item_text = raw_item.strip()
            if not item_text:
                continue
            if "/" not in item_text:
                raise ValueError("Each item must look like `18/20`.")
            raw_score, raw_total = item_text.split("/", 1)
            try:
                score = float(raw_score.strip())
                total = float(raw_total.strip())
            except ValueError as exc:
                raise ValueError("Item scores and totals must be numbers.") from exc
            if total <= 0:
                raise ValueError("Each item total must be greater than 0.")
            items.append((score, total))

        if not items:
            raise ValueError("Each category needs at least one item.")

        categories.append(GradeCategory(weight=weight, items=items))

    if not categories:
        raise ValueError("At least one category is required.")
    if len(categories) > 10:
        raise ValueError("You can only save up to 10 categories.")
    return categories


def compute_category_average(category: GradeCategory) -> float:
    total_percent = sum(calculate_percentage(score, total) for score, total in category.items)
    return total_percent / len(category.items)


def compute_class_standing_from_categories(categories: list[GradeCategory]) -> tuple[float, float, list[float]]:
    total_weight = sum(category.weight for category in categories)
    category_averages = [compute_category_average(category) for category in categories]
    class_standing = sum(
        average * (category.weight / 100.0)
        for category, average in zip(categories, category_averages)
    )
    return class_standing, total_weight, category_averages


def required_exam_percentage_for_target(
    *,
    mode: str,
    target_grade: float,
    class_standing: float,
    previous_term_average: float = 0.0,
) -> float:
    normalized_mode = normalize_mode(mode)
    if normalized_mode == "prelim":
        return (target_grade - class_standing * 0.5) / 0.5
    return (target_grade - previous_term_average / 3 - class_standing / 3) / (1 / 3)
