STRING_OPTION = 3
NUMBER_OPTION = 10


def build_command_definitions() -> list[dict]:
    return [
        {
            "name": "ping",
            "description": "Check whether the bot is online",
        },
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
