STRING_OPTION = 3
NUMBER_OPTION = 10


def build_command_definitions() -> list[dict]:
    return [
        {
            "name": "grade",
            "description": "Calculate a grade percentage and equivalence",
            "options": [
                {
                    "type": NUMBER_OPTION,
                    "name": "score",
                    "description": "Points earned",
                    "required": True,
                },
                {
                    "type": NUMBER_OPTION,
                    "name": "total",
                    "description": "Total possible points",
                    "required": True,
                },
            ],
        },
        {
            "name": "grade-save",
            "description": "Save a subject profile for later grade calculations",
            "options": [
                {
                    "type": STRING_OPTION,
                    "name": "subject",
                    "description": "Subject name, like Thermo",
                    "required": True,
                    "autocomplete": True,
                },
                {
                    "type": STRING_OPTION,
                    "name": "mode",
                    "description": "Use prelim or term",
                    "required": True,
                },
                {
                    "type": NUMBER_OPTION,
                    "name": "class_standing",
                    "description": "Current class standing average percent",
                    "required": True,
                },
                {
                    "type": NUMBER_OPTION,
                    "name": "exam_total",
                    "description": "Final exam total score, like 50 or 100",
                    "required": True,
                },
                {
                    "type": NUMBER_OPTION,
                    "name": "previous_term_average",
                    "description": "Previous term average percent for term mode",
                    "required": False,
                },
                {
                    "type": STRING_OPTION,
                    "name": "target_equivalence",
                    "description": "Target equivalence, like 1.75 or 3.00",
                    "required": False,
                },
            ],
        },
        {
            "name": "grade-show",
            "description": "Show a saved subject profile",
            "options": [
                {
                    "type": STRING_OPTION,
                    "name": "subject",
                    "description": "Saved subject name",
                    "required": True,
                    "autocomplete": True,
                }
            ],
        },
        {
            "name": "grade-required",
            "description": "Show the required exam score for a saved subject",
            "options": [
                {
                    "type": STRING_OPTION,
                    "name": "subject",
                    "description": "Saved subject name",
                    "required": True,
                    "autocomplete": True,
                }
            ],
        },
        {
            "name": "utang",
            "description": "Add an utang entry",
            "options": [
                {
                    "type": STRING_OPTION,
                    "name": "name",
                    "description": "Name of the person",
                    "required": True,
                    "autocomplete": True,
                },
                {
                    "type": STRING_OPTION,
                    "name": "amount",
                    "description": "Amount in peso, like 11, -1, 11.5, or 11.50",
                    "required": True,
                },
                {
                    "type": STRING_OPTION,
                    "name": "reason",
                    "description": "Reason for this debt",
                    "required": True,
                },
                {
                    "type": STRING_OPTION,
                    "name": "date_time",
                    "description": "Optional past date/time, like 2026-03-14 20:30",
                    "required": False,
                },
            ],
        },
        {
            "name": "list",
            "description": "Show one person's utang list, or everyone if left blank",
            "options": [
                {
                    "type": STRING_OPTION,
                    "name": "name",
                    "description": "Name to show",
                    "required": False,
                    "autocomplete": True,
                }
            ],
        },
        {
            "name": "clear",
            "description": "Clear all unpaid utang for one person",
            "options": [
                {
                    "type": STRING_OPTION,
                    "name": "name",
                    "description": "Name of the person",
                    "required": True,
                    "autocomplete": True,
                }
            ],
        },
        {
            "name": "remove",
            "description": "Remove one utang entry by date code",
            "options": [
                {
                    "type": STRING_OPTION,
                    "name": "name",
                    "description": "Name of the person",
                    "required": True,
                    "autocomplete": True,
                },
                {
                    "type": STRING_OPTION,
                    "name": "date",
                    "description": "Date from /list, like 03-14-26-G3",
                    "required": True,
                    "autocomplete": True,
                },
            ],
        },
        {
            "name": "paid",
            "description": "Mark one utang entry as paid by date code",
            "options": [
                {
                    "type": STRING_OPTION,
                    "name": "name",
                    "description": "Name of the person",
                    "required": True,
                    "autocomplete": True,
                },
                {
                    "type": STRING_OPTION,
                    "name": "date",
                    "description": "Date from /list, like 03-14-26-G3",
                    "required": True,
                    "autocomplete": True,
                },
            ],
        },
        {
            "name": "unpaid",
            "description": "Undo a paid mark by date code",
            "options": [
                {
                    "type": STRING_OPTION,
                    "name": "name",
                    "description": "Name of the person",
                    "required": True,
                    "autocomplete": True,
                },
                {
                    "type": STRING_OPTION,
                    "name": "date",
                    "description": "Date from /list, like 03-14-26-G3",
                    "required": True,
                    "autocomplete": True,
                },
            ],
        },
    ]
